from __future__ import annotations

from copy import deepcopy
from unittest.mock import Mock

import pytest

from scripts import qodo_reviewed as gate
from scripts.qodo_reviewed import review_completed

HEAD = "a" * 40
URL = "https://github.com/Nimbleway/webrouter/pull/123"


def evidence() -> dict:
    return {
        "pr_url": URL,
        "finding_count": 1,
        "review_session": {
            "run_id": 123,
            "status": "completed",
            "commit_sha": HEAD,
            "review_complete": True,
            "started_at": "2026-11-27T12:00:00Z",
        },
        "findings": [{"attribution_status": "pending", "action_level": "action_required"}],
    }


def test_completed_review_with_open_findings_is_reviewed() -> None:
    assert review_completed(evidence(), URL, HEAD)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "started"),
        ("status", "failed"),
        ("status", "unknown"),
        ("commit_sha", "b" * 40),
        ("review_complete", False),
        ("run_id", None),
        ("run_id", ""),
        ("run_id", True),
        ("run_id", -1),
        ("started_at", "yesterday"),
        ("started_at", "2026-11-27T12:00:00"),
        ("started_at", None),
    ],
)
def test_incomplete_stale_and_failed_sessions_cannot_pass(field: str, value: object) -> None:
    payload = evidence()
    payload["review_session"][field] = value
    assert not review_completed(payload, URL, HEAD)


@pytest.mark.parametrize(
    "changes",
    [
        {"pr_url": URL + "4"},
        {"review_session": None},
        {"findings": None},
        {"qar_operation_result_truncated": True},
        {"error": "unavailable", "review_session": None},
    ],
)
def test_missing_or_unusable_results_cannot_pass(changes: dict) -> None:
    payload = deepcopy(evidence())
    payload.update(changes)
    assert not review_completed(payload, URL, HEAD)


@pytest.mark.parametrize("changes", [{"finding_count": 2}, {"finding_count": True}, {"findings": [None]}])
def test_incomplete_finding_inventory_cannot_pass(changes: dict) -> None:
    payload = evidence()
    payload.update(changes)
    assert not review_completed(payload, URL, HEAD)


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> Mock:
    read = Mock(return_value=evidence())
    monkeypatch.setattr(gate, "read_review", read)
    monkeypatch.setenv("QODO_API_KEY", "test-only")
    return read


def test_a_push_during_verification_cannot_pass(monkeypatch: pytest.MonkeyPatch, reader: Mock) -> None:
    pulls = iter([{"state": "open", "head": {"sha": HEAD}}, {"state": "open", "head": {"sha": "b" * 40}}])
    states = []
    monkeypatch.setattr(gate, "github", lambda path: next(pulls))
    monkeypatch.setattr(gate, "publish", lambda head, state, *args: states.append((head, state)))
    gate.reconcile(123)
    assert states == [(HEAD, "pending")]


def test_completed_findings_publish_success_on_the_reviewed_commit(
    monkeypatch: pytest.MonkeyPatch,
    reader: Mock,
) -> None:
    states = []
    monkeypatch.setattr(gate, "github", lambda path: {"state": "open", "head": {"sha": HEAD}})
    monkeypatch.setattr(gate, "publish", lambda head, state, *args: states.append((head, state)))
    gate.reconcile(123)
    assert states == [(HEAD, "pending"), (HEAD, "success")]


def test_vendor_failure_leaves_a_blocking_status_without_disclosing_response(
    monkeypatch: pytest.MonkeyPatch,
    reader: Mock,
) -> None:
    states = []
    monkeypatch.setattr(gate, "github", lambda path: {"state": "open", "head": {"sha": HEAD}})
    monkeypatch.setattr(gate, "publish", lambda head, state, *args: states.append((head, state)))
    reader.side_effect = RuntimeError("private vendor response")
    with pytest.raises(RuntimeError, match="Could not verify Qodo review evidence") as caught:
        gate.reconcile(123)
    assert "private vendor response" not in str(caught.value)
    assert states == [(HEAD, "pending"), (HEAD, "error")]
