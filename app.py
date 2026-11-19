"""Web API for the California Halibut simulator.

A FastAPI app with two endpoints that the static frontend calls:
- POST /api/leslie: the age-structured model with Beverton-Holt recruitment,
  comparing a size limit against indiscriminate (0-inch) harvest.
- POST /api/schaefer: the Schaefer surplus production ODE and its MSY check.

The model code returns NumPy arrays and numbers, but FastAPI can only turn
plain Python types (dict, list, float, int, bool, str, None) into JSON, so every
result is converted with .tolist(), float() or bool() before it is returned.

The dashboard in static/ is served at /, so one server handles both the page
and the API it calls.

Run locally with: uvicorn app:app --reload
"""

from pathlib import Path

import numpy as np
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import leslie_model as lm
import schaefer

app = FastAPI(title="California Halibut Simulator")

# Read once when the server starts, not on every request. Neither the tuned
# egg survival nor the unfished population depends on the size limit, because
# both are computed with F = 0.
PARAMS = lm.read_parameters(lm.PARAMETERS_FILE)
EGG_SURVIVAL = lm.tune_egg_survival(PARAMS, lm.LEGAL_SIZE_INCHES)
UNFISHED_POPULATION = lm.unfished_population(PARAMS, lm.LEGAL_SIZE_INCHES, lm.UNFISHED_RECRUITS)


class LeslieRequest(BaseModel):
    fishing_mortality: float = Field(0.3, ge=0, le=5, description="Instantaneous fishing mortality F, per year")
    size_limit_inches: float = Field(lm.LEGAL_SIZE_INCHES, ge=0, le=40, description="Minimum legal length")
    steepness: float = Field(lm.STEEPNESS, gt=0.2, lt=1.0, description="Beverton-Holt steepness h")
    years: int = Field(lm.SIMULATION_YEARS, ge=1, le=500)


class SchaeferRequest(BaseModel):
    growth_rate: float = Field(schaefer.GROWTH_RATE, gt=0, le=5, description="Intrinsic growth rate r, per year")
    carrying_capacity: float = Field(schaefer.CARRYING_CAPACITY, gt=0, description="Carrying capacity K, fish")
    harvest: float = Field(200_000, ge=0, description="Constant harvest H, fish per year")
    initial_population: float | None = Field(None, gt=0, description="Starting population; defaults to K")
    years: int = Field(schaefer.SIMULATION_YEARS, ge=1, le=2000)


def run_scenario(size_limit, fishing_mortality, alpha, beta, years):
    """Simulate one harvest rule from the unfished population and package the results as JSON-ready types."""
    history = lm.simulate_density_dependent(PARAMS, fishing_mortality, size_limit, alpha, beta,
                                            UNFISHED_POPULATION, years)
    catch = lm.catch_in_numbers(PARAMS, history, fishing_mortality, size_limit)

    recruits = lm.equilibrium_recruits(PARAMS, fishing_mortality, size_limit, alpha, beta)
    equilibrium_population = recruits * lm.survivorship(PARAMS, fishing_mortality, size_limit)
    equilibrium_total = equilibrium_population.sum()
    eggs_per_recruit = lm.eggs_per_recruit(PARAMS, fishing_mortality, size_limit)
    unfished_eggs_per_recruit = lm.eggs_per_recruit(PARAMS, 0.0, size_limit)

    # Linear model with the tuned (unfished) egg survival: below 1 means it collapses.
    linear = lm.build_leslie_matrix(PARAMS, fishing_mortality, size_limit, EGG_SURVIVAL)
    # Linear model with the low-density egg survival alpha: above 1 means a small
    # population can grow, so the Beverton-Holt population persists.
    low_density = lm.build_leslie_matrix(PARAMS, fishing_mortality, size_limit, alpha)

    final_total = history[-1].sum()
    return {
        "size_limit_inches": size_limit,
        "fished_ages": PARAMS["age"][PARAMS["length"] >= size_limit].tolist(),
        "total_population": history.sum(axis=1).tolist(),
        "catch": catch.tolist(),
        "age_distribution": {
            "final": history[-1].tolist(),
            "equilibrium": equilibrium_population.tolist(),
        },
        "equilibrium": {
            "recruits": float(recruits),
            "total_population": float(equilibrium_total),
            "catch": float(lm.catch_in_numbers(PARAMS, equilibrium_population, fishing_mortality, size_limit)),
            "spawning_potential_ratio": float(eggs_per_recruit / unfished_eggs_per_recruit),
            "persists": bool(alpha * eggs_per_recruit > 1),
        },
        "eigenvalues": {
            "linear": float(lm.dominant_eigenvalue(linear)),
            "low_density": float(lm.dominant_eigenvalue(low_density)),
        },
        # How far the last simulated year is from the analytic equilibrium. Large
        # values mean the simulation has not settled yet (common near collapse).
        "final_vs_equilibrium": float((final_total - equilibrium_total) / equilibrium_total)
                                if equilibrium_total > 0 else None,
    }


@app.post("/api/leslie")
def leslie(request: LeslieRequest):
    """Run the chosen size limit and an indiscriminate (0-inch) baseline at the same F."""
    alpha, beta = lm.beverton_holt_parameters(PARAMS, lm.LEGAL_SIZE_INCHES, request.steepness,
                                              lm.UNFISHED_RECRUITS)

    # Check 1: the tuned linear model is stable when unfished (eigenvalue 1), and
    # the tuned egg survival matches the hand-derived value 1 / (eggs per recruit).
    unfished_matrix = lm.build_leslie_matrix(PARAMS, 0.0, lm.LEGAL_SIZE_INCHES, EGG_SURVIVAL)
    unfished_eggs_per_recruit = lm.eggs_per_recruit(PARAMS, 0.0, lm.LEGAL_SIZE_INCHES)

    # Check 2: with no fishing, the stable age distribution (eigenvector) matches
    # the theoretical survivorship curve l(a) = exp(-sum of M).
    survivorship = lm.survivorship(PARAMS, 0.0, lm.LEGAL_SIZE_INCHES)
    theoretical = survivorship / survivorship.sum()
    eigenvector = lm.stable_age_distribution(unfished_matrix)

    # Check 3: 20% of unfished egg production gives steepness x unfished recruits.
    unfished_eggs = lm.UNFISHED_RECRUITS * unfished_eggs_per_recruit
    steepness_model = lm.beverton_holt(0.2 * unfished_eggs, alpha, beta) / lm.UNFISHED_RECRUITS

    return {
        "inputs": request.model_dump(),
        "years": list(range(request.years + 1)),
        "ages": PARAMS["age"].tolist(),
        "lengths_inches": PARAMS["length"].tolist(),
        "unfished_age_distribution": UNFISHED_POPULATION.tolist(),
        "size_limit": run_scenario(request.size_limit_inches, request.fishing_mortality,
                                   alpha, beta, request.years),
        "indiscriminate": run_scenario(0.0, request.fishing_mortality, alpha, beta, request.years),
        "checks": {
            "unfished_eigenvalue": float(lm.dominant_eigenvalue(unfished_matrix)),
            "tuned_egg_survival": float(EGG_SURVIVAL),
            "analytic_egg_survival": float(1 / unfished_eggs_per_recruit),
            "survivorship_curve": {
                "theoretical": theoretical.tolist(),
                "eigenvector": eigenvector.tolist(),
                "max_difference": float(np.max(np.abs(theoretical - eigenvector))),
            },
            "steepness": {"expected": request.steepness, "model": float(steepness_model)},
        },
    }


@app.post("/api/schaefer")
def schaefer_model(request: SchaeferRequest):
    """Integrate dP/dt = rP(1 - P/K) - H and compare the outcome with the MSY prediction.

    Setting dP/dt = 0 gives two equilibria, P = (K/2)(1 +/- sqrt(1 - H/MSY)),
    which exist only when H <= MSY = rK/4. The upper one is stable and the lower
    one is a tipping point: a population that starts below it crashes even when
    H < MSY. Just above MSY the decline is very slow, so a crash can be
    predicted but still lie beyond the simulated horizon.
    """
    r, K, harvest = request.growth_rate, request.carrying_capacity, request.harvest
    initial = K if request.initial_population is None else request.initial_population
    msy = schaefer.maximum_sustainable_yield(r, K)

    sol = schaefer.simulate(r, K, harvest, initial, request.years,
                            t_eval=np.linspace(0, request.years, 201))
    times, population = sol.t, sol.y[0]
    crashed = sol.status == 1
    crash_year = None
    if crashed:
        # t_eval stops before the crash, so add the moment the population hits 0.
        crash_year = float(sol.t_events[0][0])
        times = np.append(times, crash_year)
        population = np.append(population, 0.0)

    if harvest <= msy:
        root = np.sqrt(1 - harvest / msy)
        upper, lower = float(K / 2 * (1 + root)), float(K / 2 * (1 - root))
        crash_expected = initial < lower
    else:
        upper = lower = None
        crash_expected = True

    return {
        "inputs": request.model_dump(),
        "time": times.tolist(),
        "population": population.tolist(),
        "msy": msy,
        "population_at_msy": K / 2,
        "harvest_over_msy": harvest / msy,
        "equilibria": {"stable": upper, "tipping_point": lower},
        "crashed": bool(crashed),
        "crash_year": crash_year,
        "msy_check": {
            "crash_expected": bool(crash_expected),
            "crashed_within_horizon": bool(crashed),
        },
    }


# Serve the dashboard (static/index.html, style.css, app.js) at /. This must come
# after the API routes: FastAPI checks routes in order, so /api/... is matched
# first and everything else falls through to the static files.
app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent / "static", html=True), name="static")
