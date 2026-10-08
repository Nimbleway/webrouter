"""Choosing a backend: defaults, per-call overrides, config and the escape hatch.

Four ways to say which backend serves a query, and one way to reach an option
web-router has not normalized. Runs with no credentials -- it routes to whatever
`configured_providers()` says this environment can use.

Run it with::

    make setup   # in your web-router-alpha clone: every extra
    python examples/routing.py
"""

from __future__ import annotations

from typing import Any

from web_router import Provider, ProviderError, UnsupportedParameterError, WebRouter, configured_providers
from web_router.types import SearchResponse

QUERY = "what is retrieval augmented generation"

USABLE = [name for name in configured_providers() if name != "auto"]

# Never empty in practice -- the keyless backends need no credential.
if not USABLE:  # pragma: no cover
    raise SystemExit("no usable provider: run make setup in your web-router-alpha clone")


def _brief(exc: Exception) -> str:
    """One readable line. An MCP error body arrives with newlines in it."""
    return " ".join(str(exc).split())[:70]


def _searched(router: WebRouter, **options: Any) -> SearchResponse | None:
    """Search, or report a backend that cannot answer and return ``None``.

    With no keys every section here routes through an anonymous tier, and those
    are rate-limited -- one being throttled must not end the tour. A named
    backend is never demoted to another, so it surfaces rather than being
    routed around.
    """
    try:
        return router.search(QUERY, **options)
    except ProviderError as exc:
        print(f"   unavailable: {_brief(exc)}")
        return None


def the_default() -> None:
    """Name nothing and `auto` picks: your keyed backends first, then the keyless ones."""
    print("\n1. no provider named -- `auto` chooses")
    with WebRouter() as router:
        response = router.search(QUERY, max_results=3)

    # Never "auto": the backend that actually answered.
    print(f"   served by {response.provider}  ({len(response)} results)")


def named_at_construction() -> None:
    """The common case: one backend for the life of the router."""
    print("\n2. named once, at construction")
    with WebRouter(USABLE[0]) as router:
        response = _searched(router, max_results=3)
    if response:
        print(f"   {response.provider}: {len(response)} results")


def per_call_override() -> None:
    """One router, a different backend for one query."""
    print("\n3. per-call override")
    with WebRouter(USABLE[0]) as router:
        for name in USABLE:
            try:
                response = router.search(QUERY, max_results=2, provider=name)
            # One backend being down must not stop the tour.
            except Exception as exc:
                print(f"   {name:<11} unavailable: {_brief(exc)}")
                continue
            print(f"   {name:<11} {len(response)} results")


def a_provider_you_built() -> None:
    """Pass an instance when its settings should travel with it.

    A provider you construct is yours: the router uses it, never caches it, and
    `close()` leaves it open for you to close.
    """
    print("\n4. an already-constructed provider")
    from web_router.registry import create_provider

    provider = create_provider(USABLE[0], timeout=30)
    try:
        with WebRouter() as router:
            response = _searched(router, max_results=2, provider=provider)
        if response:
            print(f"   {response.provider}: {len(response)} results (timeout=30 travelled with it)")
    finally:
        provider.close()


def configured_per_provider() -> None:
    """Credentials and client settings, keyed by the provider they belong to."""
    print("\n5. provider_config")
    router = WebRouter(
        USABLE[0],
        provider_config={
            # Only the backends you actually call are ever constructed, so
            # configuring one you never use costs nothing.
            USABLE[0]: {"timeout": 20},
            Provider.TAVILY: {"allow_keyless": True},
        },
    )
    with router:
        response = _searched(router, max_results=2)
    if response:
        print(f"   {response.provider}: {len(response)} results")


def the_escape_hatch() -> None:
    """provider_options goes straight to the API, untranslated.

    Use it for anything web-router has not normalized. The portability promise
    does not cover it -- these keys are provider-specific by definition.
    """
    print("\n6. provider_options, for what is not normalized")
    name = USABLE[0]
    with WebRouter(name) as router:
        try:
            _searched(router, max_results=2, country="US")
            print(f"   country='US'  -> {name} maps it")
        except UnsupportedParameterError:
            print(f"   country='US'  -> {name} does not map it, so it raised rather than ignoring it")

        response = _searched(router, max_results=2, provider_options={"safesearch": "off"})
        if response:
            print(f"   provider_options -> passed through untranslated ({len(response)} results)")


def main() -> None:
    print(f"usable here: {', '.join(USABLE)}")
    the_default()
    named_at_construction()
    per_call_override()
    a_provider_you_built()
    configured_per_provider()
    the_escape_hatch()


if __name__ == "__main__":
    main()
