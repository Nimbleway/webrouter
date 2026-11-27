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


def test_a_delayed_wakeup_keeps_current_fresh_approval_armed() -> None:
    assert decide(pull_request(), label_actor_id=QODO_ACTOR_ID).name == "ready"


def test_only_a_live_label_from_qodo_can_arm_auto_merge() -> None:
    assert decide(pull_request(), label_actor_id=QODO_ACTOR_ID).name == "ready"
    assert decide(pull_request(), label_actor_id="123").name == "disable"


def test_removing_the_label_revokes_auto_merge_on_the_same_commit() -> None:
    assert decide(pull_request(labels=[])).name == "disable"


def test_drafts_are_not_armed_even_with_qodo_approval() -> None:
    assert decide(pull_request(draft=True), label_actor_id=QODO_ACTOR_ID).name == "disable"


def test_a_new_head_without_review_evidence_is_not_armed_by_a_retained_label() -> None:
    assert (
        decide(pull_request(head={"sha": "b" * 40}), label_actor_id=QODO_ACTOR_ID, review_started_at=None).name
        == "disable"
    )


def test_closed_pull_requests_are_ignored() -> None:
    assert decide(pull_request(state="closed")).name == "ignore"


@pytest.mark.parametrize("base", ["integration", "release"])
def test_any_wakeup_on_a_non_main_target_cleans_up_armed_auto_merge(base: str) -> None:
    assert decide(pull_request(base={"ref": base}), label_actor_id=QODO_ACTOR_ID).name == "disable"


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
            pull_request(), label_actor_id=QODO_ACTOR_ID, review_started_at=review_start, label_applied_at=label_time
        ).name
        == "disable"
    )


def test_current_approval_survives_retarget_into_main_without_waiting_for_another_event() -> None:
    assert decide(pull_request(base={"ref": "main"}), label_actor_id=QODO_ACTOR_ID).name == "ready"
