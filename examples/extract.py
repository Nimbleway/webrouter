"""Fetching page contents with ``extract()``.

Needs no credentials: with no key the default ``auto`` provider falls back to
the anonymous tiers -- Tavily's keyless mode, then Jina Reader.

Run it with::

    python examples/extract.py https://example.com https://www.iana.org/domains/reserved
"""

from __future__ import annotations

import sys

from web_router import (
    UnsupportedFormatError,
    WebRouter,
    available_extract_providers,
    configured_extract_providers,
)

DEFAULT_URLS = [
    "https://example.com",
    "https://www.iana.org/domains/reserved",
    # Deliberately unreachable, so the default run actually shows what a partly
    # successful extract looks like rather than only describing it.
    "https://not-a-real-domain-zzz9.invalid",
]


def main() -> None:
    urls = sys.argv[1:] or DEFAULT_URLS

    with WebRouter() as router:
        # Several URLs in one call. Backends that take one URL per request are
        # fanned out for you; the documents come back in the order asked for.
        response = router.extract(urls, format="markdown")

        print(f"served by: {response.provider}  documents: {len(response)}")
        for document in response.documents:
            print(f"\n{document.url}  [{document.format}, {len(document.content)} chars]")
            print(f"  {document.title or '(no title)'}")
            print(f"  {document.content[:200].strip()}")

        # A URL that could not be fetched is reported rather than dropped, and
        # the ones that worked still come back.
        for failure in response.failures:
            print(f"\nfailed: {failure.url}\n  {failure.error[:160]}")

        # A backend that cannot produce the format you asked for says so, rather
        # than returning a different representation under the same name.
        try:
            router.extract(urls[0], format="html")
        except UnsupportedFormatError as exc:
            print(f"\nformat rejected rather than substituted:\n  {exc}")

    print(f"\nextract backends built in: {', '.join(available_extract_providers())}")
    print(f"usable in this environment:  {', '.join(configured_extract_providers())}")


if __name__ == "__main__":
    main()
