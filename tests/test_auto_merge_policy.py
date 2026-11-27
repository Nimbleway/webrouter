from __future__ import annotations

import pytest

from scripts.auto_merge_policy import QODO_ACTOR_ID, QODO_LABEL, decide

HEAD = "a" * 40


def pull_request(**changes: object) -> dict[str, object]:
    return {
        "state": "open",
        "draft": False,
        "head": {"sha": HEAD},
        "base": {"ref": "main"},
        "labels": [{"name": QODO_LABEL}],
        **changes,
    }


@pytest.mark.parametrize("action", ["opened", "reopened", "synchronize"])
def test_new_state_disables_auto_merge_even_with_a_label(action: str) -> None:
    assert decide(action, HEAD, None, pull_request(), label_actor_id=QODO_ACTOR_ID).name == "disable"


def test_only_a_live_label_from_qodo_can_arm_auto_merge() -> None:
    assert decide("labeled", HEAD, QODO_LABEL, pull_request(), label_actor_id=QODO_ACTOR_ID).name == "ready"
    assert decide("labeled", HEAD, QODO_LABEL, pull_request(), label_actor_id="123").name == "disable"


def test_removing_the_label_revokes_auto_merge_on_the_same_commit() -> None:
    assert decide("unlabeled", HEAD, QODO_LABEL, pull_request(labels=[])).name == "disable"


def test_a_delayed_removal_cannot_undo_a_newer_authenticated_label() -> None:
    assert decide("unlabeled", HEAD, QODO_LABEL, pull_request(), label_actor_id=QODO_ACTOR_ID).name == "ready"


def test_drafts_are_not_armed_even_with_qodo_approval() -> None:
    assert (
        decide("converted_to_draft", HEAD, None, pull_request(draft=True), label_actor_id=QODO_ACTOR_ID).name
        == "disable"
    )


def test_stale_events_and_closed_pull_requests_are_ignored() -> None:
    assert decide("labeled", "b" * 40, QODO_LABEL, pull_request()).name == "ignore"
    assert decide("labeled", HEAD, QODO_LABEL, pull_request(state="closed")).name == "ignore"


def test_integration_branches_are_ignored_but_retargeting_cleans_up() -> None:
    pull = pull_request(base={"ref": "integration"})
    assert decide("labeled", HEAD, QODO_LABEL, pull).name == "ignore"
    assert decide("edited", HEAD, None, pull, previous_base="main").name == "disable"
    assert decide("edited", HEAD, None, pull_request(), previous_base="integration").name == "disable"
