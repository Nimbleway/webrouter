"""web-router in one file: the guided tour of both methods.

Runs with no credentials at all. Set NIMBLE_API_KEY, TAVILY_API_KEY,
BRAVE_SEARCH_API_KEY and so on to bring more backends into the tour.

Run it with::

    make setup   # in your webrouter clone: every extra
    python examples/main.py
"""

import asyncio

from web_router import (
    AsyncWebRouter,
    ProviderError,
    UnsupportedFormatError,
    UnsupportedParameterError,
    WebRouter,
    available_extract_providers,
    available_providers,
    configured_extract_providers,
    configured_providers,
)

QUERY = "what is retrieval augmented generation"

# Stable pages for the extract tour when no search backend will serve one --
# the section is about extract, and should not fail on the step before it.
FALLBACK_URLS = ["https://example.com", "https://www.iana.org/domains/reserved"]

# What this environment can actually use, rather than a key map maintained here.
# `auto` is left out of the per-provider tour: it delegates to the others.
CONFIGURED = [name for name in configured_providers() if name != "auto"]

# Never empty in practice -- the keyless backends need no credential -- but the
# tour reads better failing on its own terms than on an IndexError.
if not CONFIGURED:  # pragma: no cover
    raise SystemExit("no usable provider: run make setup in your webrouter clone")

FIRST = CONFIGURED[0]

# The extract backends are a different set: `brave` searches and cannot
# extract, `jina` extracts and cannot search.
CONFIGURED_EXTRACT = [name for name in configured_extract_providers() if name != "auto"]


def _brief(exc: Exception) -> str:
    """One readable line. An MCP error body arrives with newlines in it."""
    return " ".join(str(exc).split())[:70]


def zero_config() -> None:
    """No key, no provider named: `auto` finds a backend that will answer."""
    print("\n1. no configuration at all")
    with WebRouter() as router:
        response = router.search(QUERY, max_results=3)

    print(f"   {len(available_providers())} providers known, {len(CONFIGURED)} usable here, plus auto")
    # `auto` tries the ones you hold keys for first, so this names whichever
    # backend actually served the query -- never "auto".
    print(f"   served by {response.provider}: {len(response)} results")


def basic() -> None:
    """The whole API: pick a provider, search, read normalized results."""
    print(f"\n2. one search, through {FIRST}")
    with WebRouter(FIRST) as router:
        try:
            response = router.search(QUERY, max_results=3)
        # Named backends are never routed around, so a throttled anonymous tier
        # surfaces here instead of quietly becoming a different backend.
        except ProviderError as exc:
            print(f"   {FIRST} unavailable: {_brief(exc)}")
            return

        print(f"   provider={response.provider}  results={len(response)}")
        for result in response.results:
            print(f"   - {result.title[:60]}")
            print(f"     {result.url}")
            print(f"     {len(result.content)} chars of content")


def same_call_every_backend() -> None:
    """The point of the library: the call does not change when the backend does."""
    print("\n3. the same call, every provider you can use")
    for name in CONFIGURED:
        with WebRouter(name) as router:
            try:
                response = router.search(QUERY, max_results=3)
            except ProviderError as exc:
                # Every backend fails the same way, so one bad key does not stop the tour.
                print(f"   {name:<11} unavailable: {_brief(exc)}")
                continue
            top = response.results[0]
            print(f"   {name:<11} {len(response)} results | {len(top.content):>6} chars | {top.url[:46]}")


def depth_and_escape_hatch() -> None:
    """search_depth is normalized; anything a provider does not map is rejected, not ignored."""
    print("\n4. content depth, and what happens at the edges")
    name = FIRST
    with WebRouter(name) as router:
        for depth in ("lite", "fast", "deep"):
            try:
                response = router.search(QUERY, max_results=2, search_depth=depth)
                print(f"   {name} depth={depth:<5} -> {len(response.results[0].content):>6} chars")
            except UnsupportedParameterError as exc:
                print(f"   {name} depth={depth:<5} -> not mapped: {exc}")
            except ProviderError as exc:
                print(f"   {name} depth={depth:<5} -> {_brief(exc)}")

        # Nothing is ever silently dropped. An option this provider does not
        # map raises; one it does map just works.
        try:
            router.search(QUERY, max_results=2, country="US")
            print(f"   country='US'     -> {name} maps it, so the search ran")
        except UnsupportedParameterError:
            print(f"   country='US'     -> {name} does not map it, so it raised instead of ignoring it")
        except ProviderError as exc:
            print(f"   country='US'     -> {_brief(exc)}")

        # ...and provider_options is the way through to anything web-router
        # has not normalized yet.
        try:
            response = router.search(QUERY, max_results=2, provider_options={"x_unused": "ignored"})
        except ProviderError as exc:
            print(f"   provider_options -> {_brief(exc)}")
            return
        print(f"   provider_options -> passes straight to the API ({len(response)} results)")

        # The provider's untouched payload is always kept.
        print(f"   response.raw     -> {sorted(response.raw or {})[:4]}")


def extracting() -> None:
    """The other method: pages for URLs you already have, normalized the same way."""
    print("\n5. extract: the pages behind the results")
    with WebRouter(FIRST) as searcher:
        try:
            urls = searcher.search(QUERY, max_results=2).urls
        except ProviderError:
            urls = FALLBACK_URLS

    for name in CONFIGURED_EXTRACT:
        with WebRouter(name) as extractor:
            try:
                # No format named, so this backend applies its own -- which the
                # documents then report, rather than leaving you to guess. And
                # one bad URL alongside the good ones: a partial failure is
                # reported rather than costing you the pages that worked.
                response = extractor.extract([*urls, "https://not-a-real-domain-zzz9.invalid"])
            except ProviderError as exc:
                # As in the search tour: one unusable backend does not stop it.
                print(f"   {name:<11} unavailable: {_brief(exc)}")
                continue

            print(f"   served by {response.provider}: {len(response)} of {len(urls) + 1} URLs")
            for document in response.documents:
                print(f"   - [{document.format}] {len(document.content):>6} chars | {document.url[:52]}")
            for failure in response.failures:
                print(f"   - failed: {failure.url}")

            offered = extractor.get_extract_provider().supported_formats
            print(f"   {name} returns: {', '.join(sorted(offered))}")

            # Asking for one it does not return raises before any request is
            # made, rather than coming back as a different representation under
            # the name you asked for.
            unsupported = [fmt for fmt in ("markdown", "html", "text") if fmt not in offered]
            if not unsupported:
                print(f"   {name} returns every format, so nothing to reject here")
                return
            try:
                extractor.extract(urls[0], format=unsupported[0])
            except UnsupportedFormatError as exc:
                print(f"   format={unsupported[0]!r} -> {exc}")
            return

    print("   no extract backend here could answer")  # pragma: no cover


def which_backends() -> None:
    """A name serves one method or both, and each method says which."""
    print("\n6. the two registries")
    for method, known, usable in (
        ("search", available_providers(), CONFIGURED),
        ("extract", available_extract_providers(), CONFIGURED_EXTRACT),
    ):
        print(f"   {method:<8} {len(known)} known, usable here: {', '.join(usable) or 'none'}")
    print(f"   both     {', '.join(sorted(set(CONFIGURED) & set(CONFIGURED_EXTRACT))) or 'none'}")


async def concurrent() -> None:
    """Async works the same way -- here, every backend at once."""
    print("\n7. async fan-out")
    routers = [AsyncWebRouter(name) for name in CONFIGURED]
    try:
        results = await asyncio.gather(
            *(r.search(QUERY, max_results=2) for r in routers),
            return_exceptions=True,
        )
        for name, outcome in zip(CONFIGURED, results, strict=True):
            if isinstance(outcome, BaseException):
                print(f"   {name:<11} unavailable")
            else:
                print(f"   {name:<11} {len(outcome.results)} results")
    finally:
        await asyncio.gather(*(r.aclose() for r in routers))


def main() -> None:
    print(f"usable here: {', '.join(CONFIGURED)}")
    zero_config()
    basic()
    same_call_every_backend()
    depth_and_escape_hatch()
    extracting()
    which_backends()
    asyncio.run(concurrent())


if __name__ == "__main__":
    main()
