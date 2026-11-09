"""What the provider tests share: a mock transport that records each request a backend sends."""

from __future__ import annotations

import httpx
import pytest


@pytest.fixture
def sent() -> list[httpx.Request]:
    """The requests :func:`transport` has received."""
    return []


@pytest.fixture
def transport(sent: list[httpx.Request]) -> httpx.MockTransport:
    """Records every request in :func:`sent` and answers each with an empty JSON object."""
    return httpx.MockTransport(lambda request: sent.append(request) or httpx.Response(200, json={}))
