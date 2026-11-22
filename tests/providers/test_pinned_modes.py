"""Pinning a vendor mode: each declared part sends its wire, and a caller's options survive or are refused.

The vendor call is mocked at the HTTP client (the SDK's ``search`` for Tavily), so these read what would be sent.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from scripts.contracts_modes import parts
from web_router.exceptions_typed import InvalidParameterError
from web_router.providers._pinning import pinned_request
from web_router.registry import create_provider, get_provider_class
from web_router.types import SearchRequest

DECLARING = ("exa", "tavily", "parallel", "linkup", "perplexity", "keenable", "you", "brave", "deepseek")
PINS = [(name, part) for name in DECLARING for part in parts(get_provider_class(name))]
CALLER = {"caller_key": 1}
"""A ``provider_options`` key no mode writes."""


def sent(provider: Any, request: SearchRequest, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The body ``provider`` sends for ``request``, with the vendor mocked to answer an empty page."""
    bodies: list[dict[str, Any]] = []

    def answer(method: str, path: str, **kwargs: Any) -> httpx.Response:
        bodies.append(kwargs.get("json") or kwargs.get("params") or {})
        return httpx.Response(200, json={}, request=httpx.Request(method, "https://vendor.example" + path))

    def sdk_search(**kwargs: Any) -> dict[str, Any]:
        bodies.append(kwargs)
        return {"results": []}

    monkeypatch.setattr(
        provider._client,
        "search" if provider.name == "tavily" else "request",
        sdk_search if provider.name == "tavily" else answer,
    )
    provider.search(request)
    assert len(bodies) == 1, "one call to the vendor"
    return bodies[0]


def _wire(name: str, part: str) -> dict[str, Any]:
    cls = get_provider_class(name)
    return dict((cls.tiers.get(part) or cls.contents.get(part) or cls.flags[part]).wire)


@pytest.mark.parametrize(("name", "part"), PINS, ids=[f"{n}:{p}" for n, p in PINS])
def test_pinning_a_part_sends_exactly_its_wire_beside_the_callers_other_options(
    name: str, part: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = SearchRequest(query="q", provider_options=CALLER)
    plain = sent(create_provider(name, api_key="k"), request, monkeypatch)
    pinned = sent(create_provider(f"{name}:{part}", api_key="k"), request, monkeypatch)

    changed = {field: value for field, value in pinned.items() if plain.get(field) != value}
    assert changed == _wire(name, part)
    assert pinned["caller_key"] == 1


def _refused(pin: str, monkeypatch: pytest.MonkeyPatch, **options: Any) -> InvalidParameterError:
    provider = create_provider(pin, api_key="k")
    monkeypatch.setattr(provider._client, "request", lambda *a, **k: pytest.fail("sent a refused mode"))
    with pytest.raises(InvalidParameterError) as caught:
        provider.search(SearchRequest(query="q", **options))
    return caught.value


@pytest.mark.parametrize(
    ("pin", "options", "named"),
    [
        ("exa:exa-instant", {"provider_options": {"type": "auto"}}, "provider_options"),
        ("exa:exa-highlights", {"provider_options": {"contents": {"text": True}}}, "provider_options"),
        ("exa:exa-instant+lite", {"search_depth": "deep"}, "search_depth='lite'"),
        ("exa:exa-highlights", {"search_depth": "fast"}, "search_depth='lite'"),
        ("exa:exa-instant", {"effort": "high"}, "effort='high'"),
        ("parallel:parallel-turbo", {"effort": "high"}, "effort='high'"),
        ("keenable:keenable-realtime", {"effort": "high"}, "effort='high'"),
        ("linkup:linkup-deep", {"effort": "low"}, "effort='low'"),
        ("deepseek:deepseek-content", {"search_depth": "lite"}, "search_depth='lite'"),  # lite sends need_content too
        *[
            (pin, {"effort": "medium"}, "effort='medium'")  # the default effort still decides the tier's field
            for pin in ("exa:exa-instant", "parallel:parallel-turbo", "linkup:linkup-deep")
        ],
    ],
)
def test_a_mode_is_refused_before_sending_when_the_caller_set_what_it_writes(
    pin: str, options: dict[str, Any], named: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert named in str(_refused(pin, monkeypatch, **options))


def test_a_content_never_combines_with_a_depth_but_takes_the_depth_it_stands_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(InvalidParameterError, match="twice"):
        create_provider("exa:exa-highlights+lite", api_key="k")
    body = sent(
        create_provider("exa:exa-highlights", api_key="k"), SearchRequest(query="q", search_depth="lite"), monkeypatch
    )
    assert body["contents"] == {"highlights": True}
    alone = pinned_request(get_provider_class("exa"), SearchRequest(query="q"), "exa-highlights")
    assert (alone.search_depth, alone.mode) == ("lite", "exa-highlights")  # the lite limits apply to the answer


@pytest.mark.parametrize(
    ("pin", "options", "expected"),
    [
        ("perplexity:perplexity-fast+lite", {}, {"search_type": "fast"}),
        ("brave:lite+brave-extra-snippets", {}, {"extra_snippets": True}),
        ("deepseek:deepseek-content", {"search_depth": "deep"}, {"need_content": True}),
        ("exa:exa-instant", {"search_depth": "fast"}, {"type": "instant"}),
    ],
)
def test_a_flag_or_tier_combines_with_any_depth_it_does_not_overwrite(
    pin: str, options: dict[str, Any], expected: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = sent(create_provider(pin, api_key="k"), SearchRequest(query="q", **options), monkeypatch)
    assert expected.items() <= body.items()


def test_a_pin_names_a_search_backend_and_a_mode_it_declares() -> None:
    assert get_provider_class("search:exa:exa-instant").pinned == "exa-instant"
    assert get_provider_class("exa") is get_provider_class("EXA")
    with pytest.raises(InvalidParameterError, match="research"):
        get_provider_class("research:exa:exa-instant")
    with pytest.raises(InvalidParameterError, match="exa-turbo"):
        get_provider_class("exa:exa-turbo")
    with pytest.raises(InvalidParameterError, match="perplexity-context-low"):
        get_provider_class("perplexity:perplexity-context-low")  # /search has no context-size field
