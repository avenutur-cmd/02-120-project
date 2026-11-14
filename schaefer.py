"""Schaefer surplus production model for California Halibut (baseline).

Models the whole population as one number P (total fish) with logistic growth
and a constant harvest H (fish per year), in continuous time:

    dP/dt = r P (1 - P/K) - H

Surplus production r P (1 - P/K) peaks at P = K/2, where it equals rK/4: the
maximum sustainable yield (MSY). Any constant harvest above rK/4 removes more
fish than the population can ever replace, so the population crashes to 0.
"""

import numpy as np
from scipy.integrate import solve_ivp

# Carrying capacity: the unfished equilibrium total of the Leslie matrix model (main.py).
CARRYING_CAPACITY = 5_226_723

# Intrinsic growth rate (per year). Assumed value.
GROWTH_RATE = 0.2

SIMULATION_YEARS = 50


def maximum_sustainable_yield(r, K):
    """Largest constant harvest the population can sustain: rK/4, taken at P = K/2."""
    return r * K / 4


def schaefer(t, population, r, K, harvest):
    """Right-hand side of the ODE, in the (t, y) form solve_ivp expects."""
    return r * population * (1 - population / K) - harvest


def extinct(t, population, r, K, harvest):
    """Event function: crosses zero when the population hits 0."""
    return population[0]


# Stop the solver at the first downward crossing of P = 0. Past that point the
# equation would keep "harvesting" fish that do not exist and P would go negative.
extinct.terminal = True
extinct.direction = -1


def simulate(r, K, harvest, initial_population, years, t_eval=None):
    """Integrate the Schaefer model from t = 0 to t = years with solve_ivp.

    Returns the solve_ivp result: sol.t and sol.y[0] hold the times and
    populations, and sol.status is 1 if the population crashed (extinction
    event fired), 0 if it lasted the full time span.
    """
    return solve_ivp(schaefer, (0, years), [initial_population], args=(r, K, harvest),
                     events=extinct, t_eval=t_eval, rtol=1e-8, atol=1e-6)


def test_crash_above_msy():
    """Any constant harvest above rK/4 drives the population from K to 0."""
    msy = maximum_sustainable_yield(GROWTH_RATE, CARRYING_CAPACITY)
    # Just above MSY the population lingers near K/2 for centuries, so allow a long horizon.
    for multiple in [1.01, 1.1, 1.5, 2.0, 5.0]:
        harvest = multiple * msy
        sol = simulate(GROWTH_RATE, CARRYING_CAPACITY, harvest, CARRYING_CAPACITY, years=2000)
        assert sol.status == 1, f"H = {multiple} x MSY did not crash within 2000 years"
        assert len(sol.t_events[0]) == 1
        assert abs(sol.y[0, -1]) < 1e-3, f"H = {multiple} x MSY ended at P = {sol.y[0, -1]}, not 0"


def test_survives_below_msy():
    """Control: a harvest below rK/4 levels off at the upper equilibrium instead of crashing.

    Setting dP/dt = 0 gives P* = (K/2)(1 + sqrt(1 - H/MSY)).
    """
    msy = maximum_sustainable_yield(GROWTH_RATE, CARRYING_CAPACITY)
    for multiple in [0.0, 0.5, 0.9, 0.99]:
        harvest = multiple * msy
        sol = simulate(GROWTH_RATE, CARRYING_CAPACITY, harvest, CARRYING_CAPACITY, years=2000)
        equilibrium = CARRYING_CAPACITY / 2 * (1 + np.sqrt(1 - multiple))
        assert sol.status == 0, f"H = {multiple} x MSY crashed"
        assert abs(sol.y[0, -1] - equilibrium) / equilibrium < 1e-4


def main():
    msy = maximum_sustainable_yield(GROWTH_RATE, CARRYING_CAPACITY)
    print(f"Schaefer model: r = {GROWTH_RATE}, K = {CARRYING_CAPACITY:,}")
    print(f"MSY = rK/4 = {msy:,.0f} fish/year, taken at P = K/2 = {CARRYING_CAPACITY / 2:,.0f}\n")

    years = np.arange(0, SIMULATION_YEARS + 1, 10)
    print(f"{'H / MSY':>7} " + "".join(f"{'year ' + str(y):>12}" for y in years) + "   crash year")
    for multiple in [0.0, 0.5, 0.9, 1.0, 1.1, 1.5, 2.0]:
        sol = simulate(GROWTH_RATE, CARRYING_CAPACITY, multiple * msy, CARRYING_CAPACITY,
                       SIMULATION_YEARS, t_eval=years)
        # After a crash, t_eval stops early; the remaining years are 0.
        populations = np.zeros(len(years))
        populations[:len(sol.y[0])] = sol.y[0]
        crash = f"{sol.t_events[0][0]:.1f}" if sol.status == 1 else "-"
        print(f"{multiple:>7} " + "".join(f"{p:>12,.0f}" for p in populations) + f"{crash:>13}")

    test_crash_above_msy()
    test_survives_below_msy()
    print("\nAll tests passed.")


if __name__ == "__main__":
    main()
