"""A search backend may serve only some ``search_depth`` levels: another level raises before anything is sent."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar, get_args

import pytest

from tests.conftest import FakeProvider
from web_router.exceptions import UnsupportedParameterError
from web_router.providers.auto import AutoProvider
from web_router.providers.base import SearchProvider
from web_router.registry import available_providers, get_provider_class, register_provider, unregister_provider
from web_router.types import NORMALIZED_PARAMETERS, SearchDepth, SearchRequest

ALL_LEVELS = frozenset(get_args(SearchDepth))


class ShortBodies(FakeProvider):
    """Maps ``search_depth`` but has no whole-page setting, as a backend capped short of a page is."""

    name = "short"
    depth_levels: ClassVar[frozenset[SearchDepth]] = frozenset({"lite", "fast"})
    sent: ClassVar[list[SearchRequest]] = []

    def _respond(self, request: SearchRequest) -> Any:
        ShortBodies.sent.append(request)
        return super()._respond(request)


class WholePages(FakeProvider):
    name = "whole"


class NoDepth(FakeProvider):
    """Maps nothing about depth, so a level is an unmapped option, not a refused value."""

    name = "nodepth"
    supported_parameters = NORMALIZED_PARAMETERS - {"search_depth"}
    depth_levels: ClassVar[frozenset[SearchDepth]] = frozenset()


@pytest.fixture(autouse=True)
def members() -> Iterator[None]:
    ShortBodies.sent.clear()
    for cls in (ShortBodies, WholePages, NoDepth):
        register_provider(cls.name, cls)
    yield
    for cls in (ShortBodies, WholePages, NoDepth):
        unregister_provider(cls.name)


def test_a_backend_serves_every_level_unless_it_says_otherwise() -> None:
    """The default is every level, so a backend that never declares the set is unchanged by it."""
    assert SearchProvider.depth_levels == ALL_LEVELS


def test_every_built_in_backend_that_maps_depth_serves_some_level() -> None:
    """Mapping ``search_depth`` and serving no level of it refuses every value: leave the option unmapped instead."""
    for name in available_providers():
        cls = get_provider_class(name)
        if "search_depth" in cls.supported_parameters:
            assert cls.depth_levels, name
            assert cls.depth_levels <= ALL_LEVELS, name


def test_a_level_outside_the_set_raises_naming_it_and_sends_nothing() -> None:
    with pytest.raises(UnsupportedParameterError) as caught:
        ShortBodies().search(SearchRequest(query="q", search_depth="deep"))

    assert caught.value.values == {"search_depth": "deep"}
    assert "search_depth='deep'" in str(caught.value)
    assert "'lite', 'fast'" in str(caught.value), "the message lists the levels it does serve, in order"
    assert ShortBodies.sent == []


@pytest.mark.parametrize("depth", ["lite", "fast", None])
def test_a_level_inside_the_set_or_none_at_all_is_sent(depth: SearchDepth | None) -> None:
    ShortBodies().search(SearchRequest(query="q", search_depth=depth))
    assert [request.search_depth for request in ShortBodies.sent] == [depth]


def test_a_backend_that_does_not_map_depth_refuses_the_option_not_the_value() -> None:
    with pytest.raises(UnsupportedParameterError) as caught:
        NoDepth().search(SearchRequest(query="q", search_depth="lite"))

    assert caught.value.parameters == ["search_depth"]
    assert caught.value.values == {}
    assert NoDepth.refused_values(SearchRequest(query="q", search_depth="lite")) == {}


def test_auto_passes_over_a_member_that_refuses_the_level() -> None:
    provider = AutoProvider(chain=(("short", {}), ("whole", {})))
    try:
        response = provider.search(SearchRequest(query="q", search_depth="deep"))
    finally:
        provider.close()

    assert response.provider == "whole"
    assert ShortBodies.sent == [], "the member that refuses the level is never called"


def test_auto_still_sends_a_level_the_first_member_serves_to_it() -> None:
    provider = AutoProvider(chain=(("short", {}), ("whole", {})))
    try:
        response = provider.search(SearchRequest(query="q", search_depth="fast"))
    finally:
        provider.close()

    assert response.provider == "short"


def test_auto_raises_naming_the_level_when_every_member_refuses_it() -> None:
    provider = AutoProvider(chain=(("short", {}),))
    try:
        with pytest.raises(UnsupportedParameterError) as caught:
            provider.search(SearchRequest(query="q", search_depth="deep"))
    finally:
        provider.close()

    assert caught.value.provider == "auto"
    assert caught.value.values == {"search_depth": "deep"}
    assert "short (will not serve search_depth='deep')" in str(caught.value)
