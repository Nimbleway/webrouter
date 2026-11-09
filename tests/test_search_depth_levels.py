"""A search backend may serve only some values of an option it maps: another raises before anything is sent."""

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


class OneCountry(ShortBodies):
    """Refuses a value of an option beside ``search_depth``, through ``refused_options``."""

    name = "onecountry"
    refusal_reasons: ClassVar[dict[str, str]] = {"country": "It searches from GB alone."}

    @classmethod
    def refused_options(cls, request: SearchRequest) -> dict[str, Any]:
        return {"country": request.country} if request.country not in (None, "GB") else {}


class NoDepth(FakeProvider):
    """Maps nothing about depth, so a level is an unmapped option, not a refused value."""

    name = "nodepth"
    supported_parameters = NORMALIZED_PARAMETERS - {"search_depth"}
    depth_levels: ClassVar[frozenset[SearchDepth]] = frozenset()


@pytest.fixture(autouse=True)
def members() -> Iterator[None]:
    ShortBodies.sent.clear()
    for cls in (ShortBodies, WholePages, NoDepth, OneCountry):
        register_provider(cls.name, cls)
    yield
    for cls in (ShortBodies, WholePages, NoDepth, OneCountry):
        unregister_provider(cls.name)


def test_a_backend_serves_every_level_unless_it_says_otherwise() -> None:
    """The default is every level, so a backend that never declares the set is unchanged by it."""
    assert SearchProvider.depth_levels == {"lite", "fast", "deep"}
    assert FakeProvider.refused_values(SearchRequest(query="q", search_depth="deep")) == {}


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


def test_a_refused_option_value_raises_with_its_reason_beside_a_refused_level() -> None:
    with pytest.raises(UnsupportedParameterError) as caught:
        OneCountry().search(SearchRequest(query="q", country="US", search_depth="deep"))

    assert caught.value.values == {"country": "US", "search_depth": "deep"}
    assert "It searches from GB alone." in str(caught.value) and "'lite', 'fast'" in str(caught.value)
    assert ShortBodies.sent == []


def test_auto_passes_over_a_member_that_refuses_an_option_value() -> None:
    provider = AutoProvider(chain=(("onecountry", {}), ("whole", {})))
    try:
        assert provider.search(SearchRequest(query="q", country="US")).provider == "whole"
        assert provider.search(SearchRequest(query="q", country="GB")).provider == "onecountry"
    finally:
        provider.close()


def test_an_option_a_subclass_no_longer_maps_is_refused_as_an_option_not_a_value() -> None:
    """Parallel MCP inherits Parallel's value checks but maps none of their options."""
    request = SearchRequest(query="q", country="ZZ", include_domains=[f"d{n}.com" for n in range(300)])
    assert get_provider_class("parallel").refused_values(request).keys() == {"country", "include_domains"}
    assert get_provider_class("parallel-mcp").refused_values(request) == {}
