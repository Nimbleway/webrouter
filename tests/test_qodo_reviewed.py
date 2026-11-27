from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts import qodo_reviewed as gate
from scripts.qodo_reviewed import review_completed

HEAD = "a" * 40
URL = "https://github.com/Nimbleway/webrouter/pull/123"


def evidence() -> dict:
    return {
        "pr_url": URL,
        "finding_count": 1,
        "review_session": {"run_id": 123, "status": "completed", "commit_sha": HEAD, "review_complete": True},
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


@pytest.mark.parametrize("changes", [{"finding_count": 2}, {"findings": [None]}])
def test_incomplete_finding_inventory_cannot_pass(changes: dict) -> None:
    payload = evidence()
    payload.update(changes)
    assert not review_completed(payload, URL, HEAD)


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    @asynccontextmanager
    async def transport(*args: object, **kwargs: object):
        yield (None, None, None)

    @asynccontextmanager
    async def session(*args: object):
        yield SimpleNamespace(initialize=AsyncMock())

    read = AsyncMock(return_value=evidence())
    monkeypatch.setattr(gate, "streamable_http_client", transport)
    monkeypatch.setattr(gate, "ClientSession", session)
    monkeypatch.setattr(gate, "read_review", read)
    monkeypatch.setenv("QODO_API_KEY", "test-only")
    return read


async def test_a_push_during_verification_cannot_pass(monkeypatch: pytest.MonkeyPatch, reader: AsyncMock) -> None:
    pulls = iter([{"state": "open", "head": {"sha": HEAD}}, {"state": "open", "head": {"sha": "b" * 40}}])
    states = []
    monkeypatch.setattr(gate, "github", lambda path: next(pulls))
    monkeypatch.setattr(gate, "publish", lambda head, state, *args: states.append((head, state)))
    await gate.reconcile(123)
    assert states == [(HEAD, "pending")]


async def test_completed_findings_publish_success_on_the_reviewed_commit(
    monkeypatch: pytest.MonkeyPatch,
    reader: AsyncMock,
) -> None:
    states = []
    monkeypatch.setattr(gate, "github", lambda path: {"state": "open", "head": {"sha": HEAD}})
    monkeypatch.setattr(gate, "publish", lambda head, state, *args: states.append((head, state)))
    await gate.reconcile(123)
    assert states == [(HEAD, "pending"), (HEAD, "success")]


async def test_vendor_failure_leaves_a_blocking_status_without_disclosing_response(
    monkeypatch: pytest.MonkeyPatch,
    reader: AsyncMock,
) -> None:
    states = []
    monkeypatch.setattr(gate, "github", lambda path: {"state": "open", "head": {"sha": HEAD}})
    monkeypatch.setattr(gate, "publish", lambda head, state, *args: states.append((head, state)))
    reader.side_effect = RuntimeError("private vendor response")
    with pytest.raises(RuntimeError, match="Could not verify Qodo review evidence") as caught:
        await gate.reconcile(123)
    assert "private vendor response" not in str(caught.value)
    assert states == [(HEAD, "pending"), (HEAD, "error")]


@pytest.mark.parametrize("tool_error", [False, True])
async def test_tool_errors_cannot_be_mistaken_for_success(tool_error: bool) -> None:
    result = SimpleNamespace(isError=tool_error, structuredContent=evidence(), content=[])
    session = SimpleNamespace(call_tool=AsyncMock(return_value=result))
    if tool_error:
        with pytest.raises(RuntimeError):
            await gate.read_review(session, URL)
    else:
        assert gate.review_completed(await gate.read_review(session, URL), URL, HEAD)
