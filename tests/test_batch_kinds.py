"""A batch that read nothing takes its kind from every URL's failure: one dead URL never stops ``auto`` for all."""

from __future__ import annotations

from typing import Any

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
