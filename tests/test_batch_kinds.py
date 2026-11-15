"""A batch that read nothing takes its kind from every URL's failure: one dead URL never stops ``auto`` for all."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from web_router.providers.base import _Collected
from web_router.types import ExtractFailure

A, B = "https://a.test/", "https://b.test/"


def _exhausted(*failures: ExtractFailure) -> Any:
    collected = _Collected("p", [failure.url for failure in failures])
    collected.failures = list(failures)
    return collected._exhausted()


def test_one_unresolvable_url_beside_a_503_leaves_the_batch_retryable() -> None:
    error = _exhausted(
        ExtractFailure(url=A, error="nxdomain: name not resolved"),
        ExtractFailure(url=B, error="service unavailable", status_code=503),
    )
    assert error.kind != "target"
    assert error.retryable is True


def test_a_batch_that_agrees_keeps_its_kind() -> None:
    gone = _exhausted(ExtractFailure(url=A, error="nxdomain"), ExtractFailure(url=B, error="nxdomain"))
    down = _exhausted(
        ExtractFailure(url=A, error="x", status_code=503), ExtractFailure(url=B, error="x", status_code=503)
    )
    assert gone.kind == "target"
    assert down.kind == "upstream"


def test_a_sites_own_403_through_nimble_is_no_refused_key(caplog: pytest.LogCaptureFixture) -> None:
    from web_router.providers.nimble import NimbleExtractProvider
    from web_router.routing.attempts import failed
    from web_router.types import ExtractRequest

    payload = {"url": A, "status": "failed", "status_code": 403, "warnings": ["Forbidden"]}
    response = NimbleExtractProvider(api_key="k")._to_response(ExtractRequest(urls=[A]), [A], payload)
    error = _exhausted(*response.failures, ExtractFailure(url=B, error="x", status_code=403))
    assert error.kind != "auth"
    with caplog.at_level(logging.WARNING, logger="web_router"):
        failed({}, "extract", "nimble", error)
    assert "refused its credential" not in caplog.text
