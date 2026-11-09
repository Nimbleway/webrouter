"""What the presets tests share: a definition builder, the narrow backend and its fixture, a slow research fake."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import FakeExtractProvider, FakeProvider, FakeResearchProvider
from web_router import (
    WebRouter,
)
from web_router.registry import register_provider, unregister_provider


def definition(
    preset_id: str = "acme.research",
    version: str = "1.0.0",
    components: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    components = components or {"search": {"defaults": {"max_results": 5}}}
    return {
        "spec_version": 1,
        "id": preset_id,
        "version": version,
        "applies_to": list(components),
        "components": components,
        **extra,
    }


def searched(router: WebRouter) -> FakeProvider:
    provider = router.get_provider("fake")
    assert isinstance(provider, FakeProvider)
    return provider


class NarrowProvider(FakeProvider):
    """A search backend that maps ``max_results`` and nothing else, as most real ones map few options."""

    name = "narrow"
    supported_parameters = frozenset({"max_results"})


@pytest.fixture
def narrow_provider() -> Any:
    register_provider("narrow", NarrowProvider)
    yield NarrowProvider
    unregister_provider("narrow")


LOCKED = definition(
    components={"search": {"defaults": {"max_results": 5, "country": "US"}, "overridable": ["country"]}}
)


class SlowResearch(FakeResearchProvider):
    polls_before_done = 1


REF = "acme.research@1.0.0"
"""The reference :func:`definition` builds by default."""


class RecordingExtract(FakeExtractProvider):
    """The extract fake, keeping each request as the other fakes do, so a test can read what reached it."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.requests: list[Any] = []

    def _extract(self, request: Any, urls: list[str]) -> Any:
        self.requests.append(request)
        return super()._extract(request, urls)

    async def _aextract(self, request: Any, urls: list[str]) -> Any:
        self.requests.append(request)
        return await super()._aextract(request, urls)
