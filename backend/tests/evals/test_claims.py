"""When the groundedness grader reads a percentage as a CPU claim: near a CPU word, inside one clause, not
labelled as coverage, and not one of the analyzer's own thresholds cited as a threshold."""

from __future__ import annotations

import pytest

from countersign.domain.analysis import DEFAULT_THRESHOLDS
from countersign.evals.graders import ANALYZER_THRESHOLDS, CPU_AFTER, CPU_BEFORE, THRESHOLD_NEAR, cpu_claims

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "claims"),
    [
        ("p95 daily CPU 2.32% and peak 10.98% over 31 days; about $221.92/month.", [2.32, 10.98]),
        ("p95 daily CPU 3.1%, peak 12.0%: fits m5.large", [3.1, 12.0]),
        ("2.5% average CPU", [2.5]),
        ("utilization stayed at 4%", [4.0]),
        ("below the 5% idle threshold", []),  # no CPU word: not a claim
        ("97% coverage over 31 days", []),
        ("p95 daily CPU 2.32% over 31 days with 97% coverage", [2.32]),  # the coverage word is nearer the 97
        ("p95 CPU 2.3%, below the 5% idle threshold", [2.3]),  # 5 cites the rule
        ("Idle means p95 daily CPU at most 5% and peak at most 20% with 80% coverage.", []),
        (
            "CPU peak 20%",
            [20.0],
        ),  # a threshold's value with no threshold word is a claim, checked like any other
        (
            "CPU under the 7% limit",
            [7.0],
        ),  # a threshold word with a value the analyzer does not use is a claim
        ("CPU was low. Then 7% of the budget went", []),  # a sentence boundary ends the clause
        ("CPU was low; 7% of the budget", []),
        ("saves 30% of the bill", []),
    ],
)
def test_which_percentages_are_cpu_claims(text: str, claims: list[float]) -> None:
    assert cpu_claims(text) == claims


def test_near_is_measured_in_characters_inside_the_clause() -> None:
    """A CPU word up to CPU_BEFORE characters before the number, or up to CPU_AFTER after the % sign."""
    assert cpu_claims("CPU" + " " * CPU_BEFORE + "3%") == [3.0]
    assert cpu_claims("CPU" + " " * (CPU_BEFORE + 1) + "3%") == []
    assert cpu_claims("3%" + " " * CPU_AFTER + "CPU") == [3.0]
    assert cpu_claims("3%" + " " * (CPU_AFTER + 1) + "CPU") == []


def test_the_threshold_word_must_be_near_too() -> None:
    assert cpu_claims("CPU 5%" + " " * THRESHOLD_NEAR + "threshold") == []
    assert cpu_claims("CPU 5%" + " " * (THRESHOLD_NEAR + 1) + "threshold") == [5.0]
    assert cpu_claims("below" + " " * THRESHOLD_NEAR + "5% CPU") == []
    assert cpu_claims("below" + " " * (THRESHOLD_NEAR + 1) + "5% CPU") == [5.0]


def test_the_exempt_values_are_the_analyzers_own_thresholds() -> None:
    t = DEFAULT_THRESHOLDS
    assert {t.idle_p95_cpu, t.idle_max_cpu, t.rightsize_p95_cpu, t.rightsize_max_cpu} == {5, 20, 15, 40}
    assert sorted(ANALYZER_THRESHOLDS) == [5.0, 15.0, 20.0, 40.0, 80.0]  # 80% is the minimum metrics coverage
