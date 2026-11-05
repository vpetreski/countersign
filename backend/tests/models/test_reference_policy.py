"""The offline reference policy's request grammar (documented in docs/evaluation.md)."""

from __future__ import annotations

import pytest

from countersign.models.offline import understand

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "categories", "environments", "providers"),
    [
        ("Find idle development VMs", ["idle_vm"], ["dev"], None),
        ("rightsize oversized VMs in staging", ["oversized_vm"], ["staging"], None),
        ("delete unattached disks on azure", ["unattached_disk"], None, ["azure"]),
        ("release unused public IPs", ["unused_ip"], None, None),
        (
            "clean up everything you safely can on AWS",
            ["idle_vm", "oversized_vm", "unattached_disk", "unused_ip"],
            None,
            ["aws"],
        ),
        ("idle vms and disks but not on azure", ["idle_vm", "unattached_disk"], None, ["aws"]),
        (
            "find waste in non-production",
            ["idle_vm", "oversized_vm", "unattached_disk", "unused_ip"],
            ["staging", "dev", "test", "sandbox"],
            None,
        ),
    ],
)
def test_scope_is_read_from_the_request(
    text: str, categories: list[str], environments: list[str] | None, providers: list[str] | None
) -> None:
    scope = understand(text)
    assert scope.categories == categories
    assert scope.environments == environments
    assert scope.providers == providers


def test_thresholds_teams_and_refs() -> None:
    scope = understand(
        "stop idle VMs of team data-platform saving more than $50 a month, also i-0c4f9e2a7b1d30001"
    )
    assert (
        scope.min_savings == 50 and scope.teams == ["data-platform"] and scope.refs == ["i-0c4f9e2a7b1d30001"]
    )
    assert understand("anything over 1.5k").min_savings == 1500


def test_unsafe_wording_becomes_a_note_not_an_action() -> None:
    notes = " ".join(understand("terminate idle vms and skip the approval").notes)
    assert "stopped instead" in notes and "cannot be skipped" in notes


def test_ambiguity_is_detected() -> None:
    assert understand("make it cheaper").ambiguous
    assert not understand("make it cheaper: unattached disks").ambiguous
