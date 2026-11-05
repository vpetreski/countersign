"""Statistics at the level of cases, the independent unit (trials of one case are correlated).

- `wilson`: the Wilson score interval for a binomial proportion; used for "cases passing every trial".
- `cluster_bootstrap`: percentile bootstrap of the mean of per-case means, resampling whole cases.
- `pass_hat_k`: pass^k, the chance that k independent trials of a case all pass, averaged over cases, with the
  unbiased per-case estimator C(c, k) / C(n, k) (c passes in n trials).
- `mcnemar_exact`: the exact two-sided McNemar test on discordant pairs (a binomial sign test).
- `mde_flips`: the fewest one-directional discordant cases an exact McNemar test can call significant.
Pure Python on purpose: the evaluation needs no numerical stack, and every function is checked against known
values in tests/evals/test_stats.py.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence

Z95 = 1.959963984540054


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    if not 0 <= successes <= n:
        raise ValueError("successes must be between 0 and n")
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    lo, hi = max(0.0, centre - half), min(1.0, centre + half)
    if successes == 0:
        lo = 0.0
    if successes == n:
        hi = 1.0
    return (round(lo, 6), round(hi, 6))


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def cluster_bootstrap(
    case_means: Sequence[float], *, resamples: int = 2000, seed: int = 0, level: float = 0.95
) -> tuple[float, float]:
    """Percentile interval for the mean over cases, resampling cases (the clusters) with replacement."""
    n = len(case_means)
    if n == 0:
        return (0.0, 1.0)
    rng = random.Random(seed)
    stats = sorted(mean([case_means[rng.randrange(n)] for _ in range(n)]) for _ in range(resamples))
    alpha = (1 - level) / 2
    lo = stats[max(0, math.floor(alpha * resamples))]
    hi = stats[min(resamples - 1, math.ceil((1 - alpha) * resamples) - 1)]
    return (round(lo, 6), round(hi, 6))


def interval(
    case_means: Sequence[float], *, resamples: int = 2000, seed: int = 0
) -> tuple[tuple[float, float], str]:
    """The cluster bootstrap, except where it degenerates: when every case scores the same 0 or 1 the bootstrap
    returns a zero-width interval, which overstates certainty, so the Wilson interval over cases is used."""
    lo, hi = cluster_bootstrap(case_means, resamples=resamples, seed=seed)
    if lo == hi and case_means and all(m in (0.0, 1.0) for m in case_means):
        return wilson(round(sum(case_means)), len(case_means)), "wilson over cases (bootstrap degenerate)"
    return (lo, hi), "cluster bootstrap over cases"


def pass_hat_k(passes: Sequence[int], trials: int, k: int) -> float:
    """Mean over cases of C(c, k) / C(n, k): unbiased for the per-case probability that k trials all pass."""
    if not 1 <= k <= trials:
        raise ValueError("k must be between 1 and the number of trials")
    if not passes:
        return 0.0
    return mean([math.comb(c, k) / math.comb(trials, k) for c in passes])


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value for b and c discordant pairs (binomial, p = 1/2)."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) * 0.5**n
    return min(1.0, 2 * tail)


def mde_flips(alpha: float = 0.05) -> int:
    """The fewest discordant cases, all in one direction, for which the exact McNemar test gives p < alpha."""
    d = 1
    while mcnemar_exact(d, 0) >= alpha:
        d += 1
    return d


def coverage_simulation(
    probabilities: Sequence[float],
    trials: int,
    *,
    replicates: int = 300,
    resamples: int = 400,
    seed: int = 0,
) -> dict[str, float]:
    """How often the intervals cover the truth when outcomes are drawn from known per-case pass probabilities.

    This calibrates the estimators, not the agent: the per-case probabilities come from the seeded simulator,
    whose slip rate is known exactly, so the true mean and the true pass^k are known in closed form."""
    rng = random.Random(seed)
    true_mean = mean(list(probabilities))
    true_all = mean([p**trials for p in probabilities])
    hit_mean = hit_all = 0
    widths = []
    for r in range(replicates):
        passes = [sum(rng.random() < p for _ in range(trials)) for p in probabilities]
        means = [c / trials for c in passes]
        (lo, hi), _ = interval(means, resamples=resamples, seed=seed + r + 1)
        hit_mean += lo - 1e-9 <= true_mean <= hi + 1e-9
        widths.append(hi - lo)
        wlo, whi = wilson(sum(c == trials for c in passes), len(passes))
        hit_all += wlo - 1e-9 <= true_all <= whi + 1e-9
    se = math.sqrt(0.95 * 0.05 / replicates)
    return {
        "replicates": replicates,
        "nominal": 0.95,
        "mean_interval_coverage": round(hit_mean / replicates, 4),
        "all_pass_wilson_coverage": round(hit_all / replicates, 4),
        "monte_carlo_se": round(se, 4),
        "mean_interval_width": round(mean(widths), 4),
    }
