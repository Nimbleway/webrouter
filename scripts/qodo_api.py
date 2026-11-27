"""Read stored Qodo findings through the versioned managed-tools HTTP API."""

from __future__ import annotations

import json
import math
import os
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

API = "https://sdk.qodo.ai/v2/tools"
MAX_BODY_BYTES = 4 * 1024 * 1024
POLL_STATES = {"pending", "queued", "started", "working", "running", "in_progress"}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def _decode(raw: bytes) -> dict:
    if len(raw) > MAX_BODY_BYTES:
        raise RuntimeError("Qodo response exceeds the size limit")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("ok") is False or payload.get("error"):
        raise RuntimeError("Qodo returned an unsuccessful response")
    if payload.get("qar_operation_result_truncated"):
        raise RuntimeError("Qodo returned truncated results")
    return payload


def _request(url: str, key: str, deadline: float, arguments: dict | None = None) -> tuple[int, dict, dict]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Qodo read deadline exceeded")
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    body = None
    if arguments is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps({"arguments": arguments}).encode()
    try:
        with build_opener(_NoRedirect()).open(
            Request(url, data=body, headers=headers), timeout=min(30, remaining)
        ) as res:
            if res.headers.get("Qodo-Review-API-Version") != "2":
                raise RuntimeError("Qodo did not confirm API version 2")
            payload = _decode(res.read(MAX_BODY_BYTES + 1))
            if time.monotonic() >= deadline:
                raise TimeoutError("Qodo read deadline exceeded")
            return res.status, payload, dict(res.headers)
    except HTTPError as error:
        if error.code == 429:
            return 429, {}, dict(error.headers)
        raise RuntimeError(f"Qodo HTTP request failed ({error.code})") from None
    except (URLError, OSError, ValueError):
        # Transport exceptions and response bodies may contain sensitive information.
        raise RuntimeError("Qodo HTTP request failed") from None


def _result(payload: dict) -> dict:
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("error"):
        raise RuntimeError("Qodo returned an invalid result envelope")
    return result


def _operation_url(operation_id: object, location: str | None) -> str:
    if not isinstance(operation_id, str) or not operation_id or len(operation_id) > 256:
        raise RuntimeError("Qodo returned no valid operation identity")
    expected = f"{API}/operations/{quote(operation_id, safe='')}"
    candidate = urljoin(f"{API}/", location) if location else expected
    if urlsplit(candidate) != urlsplit(expected):
        raise RuntimeError("Qodo returned an unexpected operation URL")
    return candidate


def _pause(headers: dict, payload: dict, deadline: float) -> None:
    retry = next((value for name, value in headers.items() if name.lower() == "retry-after"), None)
    delay = payload.get("poll_interval", 2)
    if retry is not None:
        try:
            delay = float(retry)
        except ValueError:
            try:
                delay = (parsedate_to_datetime(retry) - datetime.now(UTC)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                raise RuntimeError("Qodo returned an invalid retry delay") from None
    if type(delay) not in (int, float) or not math.isfinite(delay) or delay < 0:
        raise RuntimeError("Qodo returned an invalid poll interval")
    delay = max(1, delay)
    if delay >= deadline - time.monotonic():
        raise TimeoutError("Qodo read deadline exceeded")
    time.sleep(delay)


def read_review(pr_url: str, deadline: float) -> dict:
    """Read existing findings, retaining and polling any accepted read operation."""
    key = os.environ.get("QODO_API_KEY")
    if not key:
        raise RuntimeError("Qodo read credential is missing")
    code, payload, headers = _request(f"{API}/get-pr-review-findings:invoke", key, deadline, {"pr_url": pr_url})
    if code == 200:
        return _result(payload)
    if code != 202:
        raise RuntimeError("Qodo did not accept the review read")
    location = next((value for name, value in headers.items() if name.lower() == "location"), None)
    operation_url = _operation_url(payload.get("operation_id"), location)
    while True:
        _pause(headers, payload, deadline)
        code, payload, headers = _request(operation_url, key, deadline)
        if code == 429:
            continue
        if code != 200:
            raise RuntimeError("Qodo returned an unexpected operation response")
        state = payload.get("status")
        if state == "completed":
            return _result(payload)
        if state not in POLL_STATES:
            raise RuntimeError("Qodo read operation did not complete successfully")
