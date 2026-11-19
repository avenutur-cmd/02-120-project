"""Leslie matrix population model for California Halibut.

Reads age-specific biological parameters from halibut_biological_parameters.csv,
builds the Leslie matrix, and projects the population forward in time. Fishing
mortality applies only to age classes whose average length is at or above the
legal size limit (22 inches).

Two versions of recruitment are modeled:
- Linear: a fixed fraction of eggs survive to age 1 (the classic Leslie matrix).
- Density-dependent: Beverton-Holt recruitment, where egg survival falls as
  egg production rises, so a fished population can level off at a new equilibrium.
"""

import csv
from pathlib import Path

import numpy as np

# Resolved relative to this file, not the current working directory, so the CSV
# is found no matter where the code is run from (including on the web server).
PARAMETERS_FILE = Path(__file__).resolve().parent / "halibut_biological_parameters.csv"
LEGAL_SIZE_INCHES = 22.0

# Instantaneous fishing mortality rate (per year). PLACEHOLDER: replace with a
# value from your sources or one estimated from the historical catch data.
FISHING_MORTALITY = 0.0

# Fraction of eggs that survive to become age-1 fish. 1.0 means every egg
# survives (raw egg counts); main() replaces it with the value from
# tune_egg_survival(), which makes the unfished population stable.
EGG_SURVIVAL_RATE = 1.0

# Number of age-1 fish produced each year by the unfished population (R0).
# PLACEHOLDER: every population count scales with this value, so it sets the
# units of the results; replace it with an abundance estimate if you find one.
UNFISHED_RECRUITS = 1_000_000

# Beverton-Holt steepness h: the fraction of unfished recruitment still produced
# when egg production is cut to 20% of unfished. Ranges from 0.2 (recruits
# proportional to eggs, no compensation) to 1.0 (recruits independent of eggs).
# PLACEHOLDER: 0.7 is a common default for flatfish; replace it with the value
# used in the CDFW California halibut stock assessment.
STEEPNESS = 0.7

SIMULATION_YEARS = 50


def read_parameters(filename):
    """Read the parameters CSV into a dict of numpy arrays, one entry per age class, sorted by age."""
    with open(filename, newline="") as file:
        rows = sorted(csv.DictReader(file), key=lambda row: int(row["Age_Class"]))
    return {
        "age": np.array([int(row["Age_Class"]) for row in rows]),
        "length": np.array([float(row["Average_Length_Inches"]) for row in rows]),
        "natural_mortality": np.array([float(row["Natural_Mortality_Rate"]) for row in rows]),
        "fecundity": np.array([float(row["Fecundity"]) for row in rows]),
    }


def fishing_rates(params, fishing_mortality, legal_size):
    """Fishing mortality for every age class: F at or above the legal size, 0 below it."""
    return np.where(params["length"] >= legal_size, fishing_mortality, 0.0)


def survival_probabilities(params, fishing_mortality, legal_size):
    """Annual survival for every age class, as an array.

    Mortality rates are instantaneous, so survival = exp(-Z), where total
    mortality Z = M + F for fish at or above the legal size and Z = M otherwise.
    """
    total_mortality = params["natural_mortality"] + fishing_rates(params, fishing_mortality, legal_size)
    return np.exp(-total_mortality)


def build_leslie_matrix(params, fishing_mortality, legal_size, egg_survival):
    """Build the Leslie matrix as an n x n numpy array.

    Row 0 holds the number of age-1 recruits each fish produces per year
    (eggs x egg survival); the subdiagonal entry [i+1, i] holds the
    probability of surviving from age class i to age class i+1. The last
    class is a plus-group (e.g. 15+): entry [n-1, n-1] lets its fish survive
    and stay in it instead of dying off the end of the matrix.
    """
    survival = survival_probabilities(params, fishing_mortality, legal_size)

    matrix = np.diag(survival[:-1], k=-1)
    matrix[0, :] = params["fecundity"] * egg_survival
    matrix[-1, -1] = survival[-1]

    return matrix


def dominant_eigenvalue(matrix):
    """Return the largest-magnitude eigenvalue: the population's yearly growth multiplier."""
    return max(abs(np.linalg.eigvals(matrix)))


def stable_age_distribution(matrix):
    """Return the eigenvector of the dominant eigenvalue, scaled to sum to 1.

    This is the fraction of the population in each age class once the linear
    model has run long enough for the dominant eigenvalue to take over.
    """
    eigenvalues, eigenvectors = np.linalg.eig(matrix)
    dominant = np.real(eigenvectors[:, np.argmax(abs(eigenvalues))])
    return dominant / dominant.sum()


def tune_egg_survival(params, legal_size, target=1.0, tolerance=1e-10):
    """Find the egg survival rate that makes the unfished dominant eigenvalue equal target.

    The eigenvalue rises steadily as egg survival rises, so bisection works:
    keep a low guess (eigenvalue too small) and a high guess (too large) and
    halve the gap until the eigenvalue is within tolerance of the target.
    Survival rates span many orders of magnitude, so we bisect on log10.
    """
    low, high = -15.0, 0.0  # log10 of egg survival: 1e-15 to 1
    for _ in range(200):
        middle = (low + high) / 2
        matrix = build_leslie_matrix(params, 0.0, legal_size, 10 ** middle)
        eigenvalue = dominant_eigenvalue(matrix)
        if abs(eigenvalue - target) < tolerance:
            break
        if eigenvalue < target:
            low = middle
        else:
            high = middle
    return 10 ** middle


def survivorship(params, fishing_mortality, legal_size):
    """Fish in each age class per age-1 recruit, when recruitment is constant every year.

    Each age class holds the chance of surviving from age 1 to that age. The
    plus-group also holds survivors from earlier years, which adds the
    geometric series 1 + p + p^2 + ... = 1 / (1 - p).
    """
    survival = survival_probabilities(params, fishing_mortality, legal_size)
    per_recruit = np.concatenate(([1.0], np.cumprod(survival[:-1])))
    per_recruit[-1] /= 1 - survival[-1]
    return per_recruit


def unfished_population(params, legal_size, recruits):
    """Starting population vector N0: the unfished age structure for a given number of age-1 fish."""
    return recruits * survivorship(params, 0.0, legal_size)


def eggs_per_recruit(params, fishing_mortality, legal_size):
    """Lifetime eggs produced by one age-1 recruit (phi). Fishing lowers it by killing spawners."""
    return params["fecundity"] @ survivorship(params, fishing_mortality, legal_size)


def beverton_holt_parameters(params, legal_size, steepness, unfished_recruits):
    """Convert steepness h and unfished recruitment R0 into Beverton-Holt alpha and beta.

    R(E) = alpha E / (1 + beta E). The parameters are chosen so that unfished
    egg production E0 = R0 x phi0 gives exactly R0 recruits, and 20% of E0
    gives h x R0 recruits.
    """
    phi0 = eggs_per_recruit(params, 0.0, legal_size)
    alpha = 4 * steepness / (phi0 * (1 - steepness))
    beta = (5 * steepness - 1) / (phi0 * unfished_recruits * (1 - steepness))
    return alpha, beta


def beverton_holt(eggs, alpha, beta):
    """Age-1 recruits produced from a year's eggs.

    alpha is egg survival when eggs are scarce; survival falls to
    alpha / (1 + beta E) as eggs E rise, so recruits level off at alpha / beta.
    """
    return alpha * eggs / (1 + beta * eggs)


def equilibrium_recruits(params, fishing_mortality, legal_size, alpha, beta):
    """Recruits per year once a population fished at F has leveled off (analytic).

    At equilibrium, eggs E = R x phi(F) and R = alpha E / (1 + beta E).
    Solving gives R = (alpha phi - 1) / (beta phi). If alpha phi <= 1, each
    recruit cannot replace itself even at low density, and the population dies out.
    """
    phi = eggs_per_recruit(params, fishing_mortality, legal_size)
    return max(0.0, (alpha * phi - 1) / (beta * phi))


def simulate(matrix, initial_population, years):
    """Project N_{t+1} = L @ N_t forward.

    Returns an array of shape (years + 1, n): row t is the population vector in year t.
    """
    history = np.zeros((years + 1, len(initial_population)))
    history[0] = initial_population
    for t in range(years):
        history[t + 1] = matrix @ history[t]
    return history


def simulate_density_dependent(params, fishing_mortality, legal_size, alpha, beta,
                               initial_population, years):
    """Project the population with Beverton-Holt recruitment.

    Each year, survivors move up one age class (the Leslie matrix with
    fecundity set to 0), and the new age-1 class is Beverton-Holt recruitment
    from the eggs spawned this year. Returns an array of shape (years + 1, n).
    """
    survival_matrix = build_leslie_matrix(params, fishing_mortality, legal_size, egg_survival=0.0)
    history = np.zeros((years + 1, len(initial_population)))
    history[0] = initial_population
    for t in range(years):
        eggs = params["fecundity"] @ history[t]
        history[t + 1] = survival_matrix @ history[t]
        history[t + 1, 0] = beverton_holt(eggs, alpha, beta)
    return history


def catch_in_numbers(params, population, fishing_mortality, legal_size):
    """Fish caught in one year (Baranov catch equation).

    Fish in an age class die at total rate Z = M + F, so a fraction
    1 - exp(-Z) die during the year, and the share F / Z of those deaths is catch.
    population can be one year's vector (returns a number) or a whole
    simulation history of shape (years + 1, n) (returns the catch in each year).
    """
    fishing = fishing_rates(params, fishing_mortality, legal_size)
    total_mortality = params["natural_mortality"] + fishing
    return np.sum(population * fishing / total_mortality * (1 - np.exp(-total_mortality)), axis=-1)


def print_matrix(matrix, params):
    """Print the matrix with age-class labels."""
    header = "age " + "".join(f"{age:>11}" for age in params["age"])
    print(header)
    for age, row in zip(params["age"], matrix):
        print(f"{age:>3} " + "".join(f"{value:>11.4g}" for value in row))


def main():
    params = read_parameters(PARAMETERS_FILE)

    raw = build_leslie_matrix(params, 0.0, LEGAL_SIZE_INCHES, EGG_SURVIVAL_RATE)
    egg_survival = tune_egg_survival(params, LEGAL_SIZE_INCHES)
    print(f"Unfished eigenvalue with egg survival {EGG_SURVIVAL_RATE}: {dominant_eigenvalue(raw):.4f}")
    print(f"Tuned egg survival rate: {egg_survival:.4e}")

    matrix = build_leslie_matrix(params, FISHING_MORTALITY, LEGAL_SIZE_INCHES, egg_survival)
    fished = params["age"][params["length"] >= LEGAL_SIZE_INCHES]
    print(f"Fishing mortality F = {FISHING_MORTALITY} applied to ages {fished[0]}-{fished[-1]} "
          f"(length >= {LEGAL_SIZE_INCHES} in)")
    print(f"Dominant eigenvalue: {dominant_eigenvalue(matrix):.10f}\n")
    print_matrix(matrix, params)

    initial_population = unfished_population(params, LEGAL_SIZE_INCHES, UNFISHED_RECRUITS)
    history = simulate(matrix, initial_population, SIMULATION_YEARS)
    totals = history.sum(axis=1)
    print(f"\n{'year':>4} {'total fish':>14} {'vs. year 0':>11}")
    for year in range(0, SIMULATION_YEARS + 1, 10):
        print(f"{year:>4} {totals[year]:>14,.0f} {totals[year] / totals[0]:>11.4f}")

    alpha, beta = beverton_holt_parameters(params, LEGAL_SIZE_INCHES, STEEPNESS, UNFISHED_RECRUITS)
    print(f"\nBeverton-Holt recruitment, steepness h = {STEEPNESS}")
    print(f"Egg survival at low density (alpha): {alpha:.4e}")
    unfished_eggs = UNFISHED_RECRUITS * eggs_per_recruit(params, 0.0, LEGAL_SIZE_INCHES)
    unfished_egg_survival = beverton_holt(unfished_eggs, alpha, beta) / unfished_eggs
    print(f"Egg survival at unfished density:    {unfished_egg_survival:.4e} "
          f"(linear model's tuned value: {egg_survival:.4e})")

    print(f"\n{'F':>5} {'total, year ' + str(SIMULATION_YEARS):>18} {'equilibrium':>14} "
          f"{'catch/year':>12}")
    for fishing_mortality in [0.0, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0]:
        history = simulate_density_dependent(params, fishing_mortality, LEGAL_SIZE_INCHES,
                                             alpha, beta, initial_population, SIMULATION_YEARS)
        recruits = equilibrium_recruits(params, fishing_mortality, LEGAL_SIZE_INCHES, alpha, beta)
        equilibrium_total = recruits * survivorship(params, fishing_mortality, LEGAL_SIZE_INCHES).sum()
        catch = catch_in_numbers(params, history[-1], fishing_mortality, LEGAL_SIZE_INCHES)
        print(f"{fishing_mortality:>5} {history[-1].sum():>18,.0f} {equilibrium_total:>14,.0f} "
              f"{catch:>12,.0f}")


if __name__ == "__main__":
    main()
