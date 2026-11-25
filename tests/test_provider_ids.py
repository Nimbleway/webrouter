"""A provider id is checked when a router is built, and by ``check_provider_id`` alone, before anything is sent.

No key is needed: building a router or checking an id reads the registry and the backends' declared modes only.
"""

from __future__ import annotations

import pytest

from web_router import AsyncWebRouter, WebRouter, check_provider_id, provider_ids
from web_router.exceptions import ProviderNotFoundError, ProviderNotInstalledError
from web_router.exceptions_typed import InvalidParameterError

ROUTERS = [WebRouter, AsyncWebRouter]
VALID = ["auto", "max", "EXA", "exa:exa-instant", "EXA:EXA-INSTANT", "search:exa:exa-instant", "exa:exa-summary"]
"""Ids a router takes as its default; ``exa-summary`` is an extract mode only, which search checks when used."""


@pytest.mark.parametrize("router", ROUTERS)
@pytest.mark.parametrize("provider", VALID)
def test_a_valid_id_builds(router: type, provider: str) -> None:
    assert router(provider).default_provider == provider.lower()


@pytest.mark.parametrize("router", ROUTERS)
@pytest.mark.parametrize("provider", ["exa:exa-instnat", "EXA:EXA-INSTNAT", "search:exa:exa-instnat"])
def test_a_mode_typo_fails_when_built(router: type, provider: str) -> None:
    with pytest.raises(InvalidParameterError, match="exa has no mode part 'exa-instnat'"):
        router(provider)


@pytest.mark.parametrize("router", ROUTERS)
@pytest.mark.parametrize("provider", ["exaa", "exaa:exa-instant", "search:exaa:exa-instant"])
def test_a_backend_typo_fails_when_built(router: type, provider: str) -> None:
    with pytest.raises(ProviderNotFoundError):
        router(provider)


def test_an_id_naming_its_method_is_checked_for_that_method_only() -> None:
    """``exa-instant`` is a search mode; Exa's extract declares none of that name."""
    with pytest.raises(InvalidParameterError, match="exa has no mode part 'exa-instant'"):
        WebRouter("extract:exa:exa-instant")
    assert WebRouter("extract:exa:exa-summary").default_provider == "extract:exa:exa-summary"


@pytest.mark.parametrize("provider", ["exa:", ":exa", "exa::exa-instant"])
def test_an_id_with_an_empty_part_fails_when_built(provider: str) -> None:
    with pytest.raises(InvalidParameterError, match="is not tool:provider"):
        WebRouter(provider)


def test_a_method_without_modes_named_by_a_default_serves_its_backend() -> None:
    """A research default named ``research:nimble`` serves ``nimble``, as the router's research getter does."""
    assert WebRouter("research:nimble").default_provider == "research:nimble"


def test_a_mode_on_a_backend_no_pinning_method_knows_is_refused() -> None:
    """``local`` only opens browsers, which take no mode."""
    with pytest.raises(InvalidParameterError, match="takes none of"):
        WebRouter("local:local-fast")


@pytest.mark.parametrize(
    ("tool", "provider"),
    [("search", "exa:exa-instant"), ("SEARCH", "exa"), ("extract", "exa:exa-summary"), ("research", "nimble")],
)
def test_check_passes_an_id_its_tool_takes(tool: str, provider: str) -> None:
    check_provider_id(provider, tool)


@pytest.mark.parametrize(
    ("tool", "provider", "error", "match"),
    [
        ("extract", "exa:exa-instant", InvalidParameterError, "no mode part 'exa-instant'"),
        ("search", "exa:exa-summary", InvalidParameterError, "no mode part 'exa-summary'"),
        ("search", "exaa", ProviderNotFoundError, "exaa"),
        ("research", "exa", ProviderNotFoundError, "does not serve"),
        ("research", "nimble:fast", InvalidParameterError, "research takes none of"),
        ("research", "research:nimble", ProviderNotFoundError, "research:nimble"),
        ("search", "exa:", InvalidParameterError, "is not tool:provider"),
        ("search", "extract:exa:exa-summary", InvalidParameterError, "names 'extract'"),
        ("searh", "exa", InvalidParameterError, "not a router method"),
    ],
)
def test_check_refuses_an_id_its_tool_does_not_take(tool: str, provider: str, error: type, match: str) -> None:
    with pytest.raises(error, match=match):
        check_provider_id(provider, tool)


def test_a_backend_not_installed_builds_but_a_named_tool_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without its extra a backend's modes can't be read: a router's default passes, a check for a tool raises."""

    def missing(name: str) -> type:
        raise ProviderNotInstalledError("exa", "exa")

    monkeypatch.setitem(provider_ids._CLASSES, "search", (missing, lambda: ["exa"]))
    assert WebRouter("exa:exa-instnat").default_provider == "exa:exa-instnat"
    assert WebRouter("search:exa:exa-instnat").default_provider == "search:exa:exa-instnat"
    with pytest.raises(ProviderNotInstalledError):
        check_provider_id("exa:exa-instant", "search")
