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


def test_exact_mcnemar_and_the_minimum_detectable_effect() -> None:
    assert stats.mcnemar_exact(0, 0) == 1.0
    assert stats.mcnemar_exact(6, 0) == pytest.approx(0.03125)
    assert stats.mcnemar_exact(5, 0) == pytest.approx(0.0625)
    assert stats.mcnemar_exact(3, 3) == 1.0
    assert stats.mcnemar_exact(10, 2) == pytest.approx(
        0.0385742, abs=1e-6
    )  # scipy.stats.binomtest(2, 12).pvalue
    assert stats.mde_flips(0.05) == 6
    assert stats.mde_flips(0.01) == 8


def test_cluster_bootstrap_and_its_boundary_fallback() -> None:
    assert stats.cluster_bootstrap([0.5] * 10) == (0.5, 0.5)
    lo, hi = stats.cluster_bootstrap([0.0, 1.0] * 15, seed=3)
    assert lo < 0.5 < hi
    (lo, hi), method = stats.interval([1.0] * 30)
    assert method.startswith("wilson") and (lo, hi) == stats.wilson(30, 30)
    (lo, hi), method = stats.interval([0.0, 1.0, 1.0, 0.5])
    assert method.startswith("cluster bootstrap") and lo <= 0.625 <= hi
    assert stats.cluster_bootstrap([]) == (0.0, 1.0)


def test_coverage_simulation_is_near_nominal_for_heterogeneous_cases() -> None:
    probs = [1.0] * 10 + [0.8] * 20
    cov = stats.coverage_simulation(probs, 10, replicates=120, resamples=200, seed=11)
    assert cov["mean_interval_coverage"] >= 0.95 - 3 * math.sqrt(0.95 * 0.05 / 120)
    assert 0 < cov["mean_interval_width"] < 0.2
