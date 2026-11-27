from __future__ import annotations

import io
import json
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from scripts import qodo_api as api

URL = "https://github.com/Nimbleway/webrouter/pull/123"
RESULT = {"review_session": None, "findings": []}


class Response(io.BytesIO):
    def __init__(self, status: int = 200, payload: object = None, headers: dict | None = None) -> None:
        super().__init__(json.dumps(payload).encode())
        self.status = status
        self.headers = {"Qodo-Review-API-Version": "2", **(headers or {})}


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Mock:
    clock = [0.0]
    monkeypatch.setenv("QODO_API_KEY", "test-only")
    monkeypatch.setattr(api.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(api.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    send = Mock()
    monkeypatch.setattr(api, "build_opener", lambda handler: SimpleNamespace(open=send))
    return send


def test_direct_success_uses_only_the_stored_result_read(transport: Mock) -> None:
    transport.return_value = Response(payload={"result": RESULT})
    assert api.read_review(URL, 30) == RESULT
    request = transport.call_args.args[0]
    assert request.full_url == api.API + "/get-pr-review-findings:invoke"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {"arguments": {"pr_url": URL}}


def test_accepted_read_is_polled_without_reposting(transport: Mock) -> None:
    transport.side_effect = [
        Response(202, {"operation_id": "op-1", "poll_interval": 1}),
        Response(payload={"status": "running", "poll_interval": 1}),
        Response(payload={"status": "completed", "result": RESULT}),
    ]
    assert api.read_review(URL, 30) == RESULT
    requests = [call.args[0] for call in transport.call_args_list]
    assert [request.get_method() for request in requests] == ["POST", "GET", "GET"]
    assert all(request.full_url == api.API + "/operations/op-1" for request in requests[1:])


@pytest.mark.parametrize("status", ["failed", "cancelled", "input_required", "unknown"])
def test_non_success_operation_states_never_return_results(transport: Mock, status: str) -> None:
    transport.side_effect = [Response(202, {"operation_id": "op-1"}), Response(payload={"status": status})]
    with pytest.raises(RuntimeError):
        api.read_review(URL, 30)


@pytest.mark.parametrize(
    "location",
    [
        "https://example.com/v2/tools/operations/op-1",
        "http://sdk.qodo.ai/v2/tools/operations/op-1",
        "/v2/tools/operations/other",
        "/v1/tools/operations/op-1",
        "/v2/tools/operations/op-1?extra=1",
    ],
)
def test_operation_location_cannot_leak_the_key_or_change_identity(transport: Mock, location: str) -> None:
    transport.return_value = Response(202, {"operation_id": "op-1"}, {"Location": location})
    with pytest.raises(RuntimeError, match="unexpected operation URL"):
        api.read_review(URL, 30)
    assert transport.call_count == 1


def test_retry_after_and_poll_rate_limits_are_respected(transport: Mock) -> None:
    transport.side_effect = [
        Response(202, {"operation_id": "op-1"}, {"Retry-After": "3"}),
        HTTPError(api.API + "/operations/op-1", 429, "limited", {"Retry-After": "4"}, None),
        Response(payload={"status": "completed", "result": RESULT}),
    ]
    assert api.read_review(URL, 30) == RESULT
    assert api.time.monotonic() == 7


def test_an_invoke_rate_limit_is_not_reposted(transport: Mock) -> None:
    transport.side_effect = HTTPError(api.API, 429, "limited", {}, None)
    with pytest.raises(RuntimeError):
        api.read_review(URL, 30)
    assert transport.call_count == 1


def test_polling_stops_at_the_deadline(transport: Mock) -> None:
    transport.side_effect = [Response(202, {"operation_id": "op-1", "poll_interval": 20})]
    with pytest.raises(TimeoutError):
        api.read_review(URL, 10)
    assert transport.call_count == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"result": None},
        {"ok": False},
        {"error": "failed"},
        {"result": RESULT, "qar_operation_result_truncated": True},
    ],
)
def test_malformed_and_error_envelopes_are_rejected(transport: Mock, payload: dict) -> None:
    transport.return_value = Response(payload=payload)
    with pytest.raises(RuntimeError):
        api.read_review(URL, 30)


@pytest.mark.parametrize("code", [302, 401, 403, 404, 500])
def test_http_errors_do_not_expose_vendor_details(transport: Mock, code: int) -> None:
    transport.side_effect = HTTPError(api.API, code, "private response", {}, io.BytesIO(b"private response"))
    with pytest.raises(RuntimeError) as caught:
        api.read_review(URL, 30)
    assert "private response" not in str(caught.value)


def test_transport_errors_do_not_expose_vendor_details(transport: Mock) -> None:
    transport.side_effect = URLError("private response")
    with pytest.raises(RuntimeError) as caught:
        api.read_review(URL, 30)
    assert "private response" not in str(caught.value)


def test_v1_or_missing_version_confirmation_cannot_pass(transport: Mock) -> None:
    transport.return_value = Response(payload={"result": RESULT}, headers={"Qodo-Review-API-Version": "1"})
    with pytest.raises(RuntimeError, match="version 2"):
        api.read_review(URL, 30)


def test_missing_credentials_never_send_a_request(monkeypatch: pytest.MonkeyPatch, transport: Mock) -> None:
    monkeypatch.delenv("QODO_API_KEY")
    with pytest.raises(RuntimeError, match="credential is missing"):
        api.read_review(URL, 30)
    transport.assert_not_called()
