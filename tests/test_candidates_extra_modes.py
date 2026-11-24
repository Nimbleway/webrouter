"""A router's ``extra_modes``: vendor modes it asks to be shown beyond the ones a backend offers.

Offline: the members are read from their classes and the shipped price list; nothing is sent.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.candidates_helpers import Shown, clear_keys, exa_tavily_fixture, ids, set_keys  # noqa: F401  (fixture)
from web_router import RouteDecision, RouteRequest, WebRouter
from web_router.exceptions import ConfigurationError
from web_router.providers._pinning import pinned_request
from web_router.registry import get_provider_class
from web_router.routing.candidates import build_candidates
from web_router.types import SearchRequest

CHAIN = {"auto": {"chain": {"search": ["exa", "tavily"]}}}
SEARCH = {"search": SearchRequest(query="q")}
COMBINED = "search:exa:exa-deep-lite+fast"


class Asking(Shown):
    """A router that asks for ``extra`` and ranks ``attempts``."""

    def __init__(self, extra: Any, attempts: tuple[str, ...] = (), **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.extra_modes, self.attempts = extra, attempts

    def route(self, req: RouteRequest) -> RouteDecision:
        super().route(req)
        return RouteDecision(attempts=list(self.attempts))


def _shown(router: Shown) -> list[str]:
    with WebRouter(provider_config=CHAIN, router=router) as wr:
        wr.plan("q", tool="search")
    return router.ids


def test_a_tier_at_a_depth_is_shown_after_its_backend_with_route_modes_off(keys: Any) -> None:
    assert _shown(Asking({"exa": ["exa-deep-lite+fast"]})) == ["search:exa", COMBINED, "search:tavily"]


def test_extra_modes_follow_the_offered_ones_each_once_however_written(keys: Any) -> None:
    extra = {"exa": ["fast+exa-deep-lite", "exa-deep-lite+fast", "exa-instant"]}
    offered = _shown(Shown(route_modes=True))
    at = offered.index("search:tavily")
    assert _shown(Asking(extra, route_modes=True)) == [*offered[:at], COMBINED, *offered[at:]]


def test_a_mode_the_backend_refuses_or_does_not_have_is_dropped_with_why_and_another_backends_is_passed_over(
    keys: Any,
) -> None:
    extra = {"exa": ["exa-nope", "exa-instant+exa-fast"], "brave": ["brave-extra-snippets"]}
    found = build_candidates(WebRouter(provider_config=CHAIN), SEARCH, extra_modes=extra)
    assert ids(found) == ["search:exa", "search:tavily"]
    assert "no mode part 'exa-nope'" in found.dropped["search:exa:exa-nope"]
    assert "refuses this mode" in found.dropped["search:exa:exa-instant+exa-fast"]


def test_an_attempt_may_name_a_mode_the_router_asked_for(keys: Any) -> None:
    router = Asking({"exa": ["exa-deep-lite+fast"]}, attempts=(COMBINED, "search:exa:exa-deep+fast"))
    with WebRouter(provider_config=CHAIN, router=router) as wr:
        decision = wr.plan("q", tool="search").decision
    assert decision.merged[0] == COMBINED
    assert list(decision.ignored) == ["search:exa:exa-deep+fast"], "one it did not ask for is still no candidate"


@pytest.mark.parametrize("extra", [["exa-fast"], {"exa": "exa-fast"}, {"exa": [1]}, {1: ["exa-fast"]}, 1])
def test_extra_modes_takes_a_mapping_of_backend_to_mode_ids(extra: Any, keys: Any) -> None:
    with pytest.raises(ConfigurationError, match="extra_modes attribute takes a mapping"):
        WebRouter(provider_config=CHAIN, router=Asking(extra))


@pytest.mark.parametrize(
    ("name", "mode", "effort"),
    [("exa", "exa-lite+fast", "low"), ("exa", "exa-deep-lite+fast", "high"), ("keenable", "keenable-pro+fast", "high")],
)
def test_a_tier_at_the_fast_depth_sends_what_that_effort_at_that_depth_sends(name: str, mode: str, effort: str) -> None:
    provider = get_provider_class(name)(api_key="k")
    by_options = SearchRequest(query="q", max_results=10, search_depth="fast", effort=effort)
    pinned = pinned_request(provider, SearchRequest(query="q", max_results=10), mode)
    assert provider._build_payload(pinned) == provider._build_payload(by_options)
