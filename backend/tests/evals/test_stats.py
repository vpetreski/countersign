"""The statistics against values computed by hand or by reference implementations."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from countersign.evals import stats

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("k", "n", "lo", "hi"),
    [
        (0, 10, 0.0, 0.277533),  # z = 1.959964, as statsmodels' proportion_confint(method='wilson')
        (10, 10, 0.722467, 1.0),
        (5, 10, 0.236593, 0.763407),
        (30, 30, 0.886487, 1.0),
    ],
)
def test_wilson_matches_known_values(k: int, n: int, lo: float, hi: float) -> None:
    got = stats.wilson(k, n)
    assert got == pytest.approx((lo, hi), abs=2e-6)


def test_wilson_rejects_impossible_counts_and_handles_empty() -> None:
    with pytest.raises(ValueError):
        stats.wilson(11, 10)
    assert stats.wilson(0, 0) == (0.0, 1.0)


@given(st.integers(min_value=1, max_value=400).flatmap(lambda n: st.tuples(st.integers(0, n), st.just(n))))
def test_wilson_contains_the_point_estimate(kn: tuple[int, int]) -> None:
    k, n = kn
    lo, hi = stats.wilson(k, n)
    assert 0 <= lo <= k / n + 1e-9 <= hi + 2e-9 <= 1 + 2e-9


def test_pass_hat_k_is_the_unbiased_combinatorial_estimator() -> None:
    passes = [3, 2, 0]
    assert stats.pass_hat_k(passes, 3, 1) == pytest.approx((1 + 2 / 3 + 0) / 3)
    assert stats.pass_hat_k(passes, 3, 2) == pytest.approx((1 + 1 / 3 + 0) / 3)
    assert stats.pass_hat_k(passes, 3, 3) == pytest.approx(1 / 3)
    with pytest.raises(ValueError):
        stats.pass_hat_k(passes, 3, 4)
    assert stats.pass_hat_k([], 3, 1) == 0.0


def test_pass_hat_k_is_unbiased_against_the_closed_form() -> None:
    """E[C(c,k)/C(n,k)] = p^k when c ~ Binomial(n, p): checked exactly by summing over the distribution."""
    n, p = 5, 0.7
    for k in range(1, n + 1):
        expected = sum(
            math.comb(n, c) * p**c * (1 - p) ** (n - c) * math.comb(c, k) / math.comb(n, k)
            for c in range(n + 1)
        )
        assert expected == pytest.approx(p**k)


def test_exact_mcnemar_and_the_minimum_significant_discordance() -> None:
    assert stats.mcnemar_exact(0, 0) == 1.0
    assert stats.mcnemar_exact(6, 0) == pytest.approx(0.03125)
    assert stats.mcnemar_exact(5, 0) == pytest.approx(0.0625)
    assert stats.mcnemar_exact(3, 3) == 1.0
    assert stats.mcnemar_exact(10, 2) == pytest.approx(
        0.0385742, abs=1e-6
    )  # scipy.stats.binomtest(2, 12).pvalue
    assert stats.min_significant_discordance(0.05) == 6
    assert stats.min_significant_discordance(0.01) == 8


def test_mcnemar_power_is_exact_for_the_quoted_example() -> None:
    """Agents passing 0.8 and 0.6 of cases, independently, on 30 cases: discordance probabilities 0.32 and 0.12,
    concordant 0.56. The power of the exact two-sided test at 0.05, enumerated over every multinomial outcome, is
    about 28%: a 20-point difference in pass rate is missed more often than it is found."""
    assert stats.mcnemar_power(0.8, 0.6, 30) == pytest.approx(0.2831776, abs=1e-7)
    assert (
        stats.mcnemar_power(0.7, 0.7, 30) < 0.05
    )  # no difference: rejections stay below alpha (exact, conservative)
    assert stats.mcnemar_power(1.0, 0.0, 6) == pytest.approx(
        1.0
    )  # 6 sure one-way flips are always significant
    assert stats.mcnemar_power(1.0, 0.0, 5) == 0.0  # 5 never are
    assert stats.mcnemar_power(0.8, 0.6, 100) > stats.mcnemar_power(0.8, 0.6, 30)


def test_mcnemar_power_matches_the_conditional_binomial_form() -> None:
    """The same number another way: the discordant count is Binomial(n, pb + pc) and, given it, the A-only count is
    Binomial(d, pb / (pb + pc)). Summing that way must agree with the joint enumeration."""
    n, pb, pc = 30, 0.32, 0.12
    by_condition = sum(
        math.comb(n, d)
        * (pb + pc) ** d
        * (1 - pb - pc) ** (n - d)
        * sum(
            math.comb(d, b) * (pb / (pb + pc)) ** b * (pc / (pb + pc)) ** (d - b)
            for b in range(d + 1)
            if stats.mcnemar_exact(b, d - b) < 0.05
        )
        for d in range(n + 1)
    )
    assert stats.mcnemar_power(0.8, 0.6, n) == pytest.approx(by_condition, abs=1e-12)


def test_cluster_bootstrap_and_its_boundary_fallback() -> None:
    assert stats.cluster_bootstrap([0.5] * 10) == (0.5, 0.5)
    lo, hi = stats.cluster_bootstrap([0.0, 1.0] * 15, seed=3)
    assert lo < 0.5 < hi
    (lo, hi), method = stats.interval([1.0] * 30)
    assert method.startswith("wilson") and (lo, hi) == stats.wilson(30, 30)
    (lo, hi), method = stats.interval([0.0, 1.0, 1.0, 0.5])
    assert method.startswith("cluster bootstrap") and lo <= 0.625 <= hi
    assert stats.cluster_bootstrap([]) == (0.0, 1.0)


def test_the_fallback_is_keyed_on_a_zero_width_bootstrap_not_on_whole_scores() -> None:
    """Every case at the same fraction collapses the bootstrap just as all-0 or all-1 does."""
    (lo, hi), method = stats.interval([0.5] * 10)
    assert method.startswith("wilson") and (lo, hi) == stats.wilson(5, 10) and lo < 0.5 < hi
    (lo, hi), method = stats.interval([2 / 3] * 30)
    assert method.startswith("wilson") and (lo, hi) == stats.wilson(20, 30)
    (lo, hi), method = stats.interval([0.0] * 30)
    assert method.startswith("wilson") and (lo, hi) == stats.wilson(0, 30)


def test_coverage_simulation_is_near_nominal_for_heterogeneous_cases() -> None:
    probs = [1.0] * 10 + [0.8] * 20
    cov = stats.coverage_simulation(probs, 10, replicates=120, resamples=200, seed=11)
    assert cov["mean_interval_coverage"] >= 0.95 - 3 * math.sqrt(0.95 * 0.05 / 120)
    assert 0 < cov["mean_interval_width"] < 0.2
