from __future__ import annotations

import pytest

from scripts.auto_merge_policy import QODO_ACTOR_ID, QODO_LABEL, Decision, decide

HEAD = "a" * 40


def pull_request(
    *,
    head: str = HEAD,
    base: str = "main",
    state: str = "open",
    draft: bool = False,
    labels: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "state": state,
        "draft": draft,
        "head": {"sha": head},
        "base": {"ref": base},
        "labels": [{"name": label} for label in labels],
    }


@pytest.mark.parametrize("action", ["opened", "reopened", "synchronize"])
def test_new_or_reopened_pull_request_resets_old_qodo_state(action: str) -> None:
    result = decide(action, HEAD, None, pull_request(labels=(QODO_LABEL,)))

    assert result == Decision("reset", HEAD, "new pull request state")


def test_stale_event_is_ignored() -> None:
    result = decide("labeled", "b" * 40, QODO_LABEL, pull_request(labels=(QODO_LABEL,)))

    assert result == Decision("ignore", HEAD, "event head is stale")


def test_stack_parent_is_ignored() -> None:
    result = decide(
        "labeled",
        HEAD,
        QODO_LABEL,
        pull_request(base="integration/feature", labels=(QODO_LABEL,)),
    )

    assert result == Decision("ignore", HEAD, "base is not main")


def test_closed_pull_request_is_ignored() -> None:
    result = decide("labeled", HEAD, QODO_LABEL, pull_request(state="closed", labels=(QODO_LABEL,)))

    assert result == Decision("ignore", HEAD, "pull request is not open")


def test_qodo_label_on_current_ready_head_is_ready() -> None:
    result = decide(
        "labeled",
        HEAD,
        QODO_LABEL,
        pull_request(labels=(QODO_LABEL,)),
        event_actor_id=QODO_ACTOR_ID,
    )

    assert result == Decision("ready", HEAD, "Qodo approved the current head")


def test_label_from_another_actor_holds() -> None:
    result = decide(
        "labeled",
        HEAD,
        QODO_LABEL,
        pull_request(labels=(QODO_LABEL,)),
        event_actor_id="1234",
    )

    assert result == Decision("hold", HEAD, "Qodo label was not applied by Qodo")


def test_qodo_label_on_draft_records_head_approval() -> None:
    result = decide(
        "labeled",
        HEAD,
        QODO_LABEL,
        pull_request(draft=True, labels=(QODO_LABEL,)),
        event_actor_id=QODO_ACTOR_ID,
    )

    assert result == Decision("approve", HEAD, "Qodo approved the current draft head")


def test_ready_for_review_uses_authenticated_head_status() -> None:
    result = decide(
        "ready_for_review",
        HEAD,
        None,
        pull_request(labels=(QODO_LABEL,)),
        event_actor_id=QODO_ACTOR_ID,
        qodo_status="success",
    )

    assert result == Decision("ready", HEAD, "Qodo approved the current head")


def test_ready_for_review_rejects_untrusted_label_history() -> None:
    result = decide(
        "ready_for_review",
        HEAD,
        None,
        pull_request(labels=(QODO_LABEL,)),
        event_actor_id="1234",
        qodo_status="success",
    )

    assert result == Decision("hold", HEAD, "Qodo label was not applied by Qodo")


def test_ready_for_review_without_qodo_approval_holds() -> None:
    result = decide("ready_for_review", HEAD, None, pull_request())

    assert result == Decision("hold", HEAD, "current head is not Qodo approved")


def test_draft_holds_untrusted_qodo_label() -> None:
    result = decide("converted_to_draft", HEAD, None, pull_request(draft=True, labels=(QODO_LABEL,)))

    assert result == Decision("hold", HEAD, "pull request is draft")


def test_base_change_into_main_resets_state() -> None:
    result = decide("edited", HEAD, None, pull_request(), previous_base="integration/feature")

    assert result == Decision("reset", HEAD, "base changed to main")


def test_title_edit_is_ignored() -> None:
    result = decide("edited", HEAD, None, pull_request())

    assert result == Decision("ignore", HEAD, "pull request metadata changed")


def test_qodo_label_removal_holds() -> None:
    result = decide("unlabeled", HEAD, QODO_LABEL, pull_request())

    assert result == Decision("hold", HEAD, "Qodo approval was removed")


def test_stale_qodo_label_removal_cannot_undo_a_newer_approval() -> None:
    result = decide("unlabeled", HEAD, QODO_LABEL, pull_request(labels=(QODO_LABEL,)))

    assert result == Decision("ignore", HEAD, "Qodo approval is present again")


@pytest.mark.parametrize("action", ["labeled", "unlabeled"])
def test_unrelated_label_event_is_ignored(action: str) -> None:
    result = decide(action, HEAD, "documentation", pull_request(labels=(QODO_LABEL,)))

    assert result == Decision("ignore", HEAD, "unrelated label event")
