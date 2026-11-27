from __future__ import annotations

from functools import partial

import pytest

from scripts.auto_merge_policy import QODO_ACTOR_ID, QODO_LABEL
from scripts.auto_merge_policy import decide as policy

HEAD = "a" * 40
START = "2026-11-27T12:00:00Z"
LABEL_TIME = "2026-11-27T12:01:00Z"
decide = partial(policy, review_started_at=START, label_applied_at=LABEL_TIME)


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


def test_stale_events_reconcile_live_state_and_closed_pull_requests_are_ignored() -> None:
    assert decide("labeled", "b" * 40, QODO_LABEL, pull_request()).name == "disable"
    assert decide("labeled", HEAD, QODO_LABEL, pull_request(state="closed")).name == "ignore"


def test_any_event_on_an_integration_branch_cleans_up_armed_auto_merge() -> None:
    pull = pull_request(base={"ref": "integration"})
    assert decide("labeled", HEAD, QODO_LABEL, pull).name == "disable"
    assert decide("edited", HEAD, None, pull, previous_base="main").name == "disable"
    assert decide("edited", HEAD, None, pull_request(), previous_base="integration").name == "disable"


@pytest.mark.parametrize(
    "review_start,label_time",
    [
        (START, "2026-11-27T11:59:00Z"),
        (None, LABEL_TIME),
        (START, None),
        ("invalid", LABEL_TIME),
        (START, "2026-11-27T12:01:00"),
    ],
)
def test_old_or_unbound_labels_never_arm_a_current_commit(review_start: str | None, label_time: str | None) -> None:
    assert (
        decide(
            "labeled",
            HEAD,
            QODO_LABEL,
            pull_request(),
            label_actor_id=QODO_ACTOR_ID,
            review_started_at=review_start,
            label_applied_at=label_time,
        ).name
        == "disable"
    )


def test_completion_wakeup_can_arm_a_label_received_before_status_publication() -> None:
    assert decide("status", HEAD, None, pull_request(), label_actor_id=QODO_ACTOR_ID).name == "ready"
