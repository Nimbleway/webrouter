"""Add your own backend, and route to it like any built-in.

The provider here is Wikipedia's open search API: no key, no account, so this
example runs as-is and you can see a custom backend working before you point one
at an API you actually pay for.

`HttpSearchProvider` owns the client, the auth header, HTTP error mapping and
cleanup. You write two methods: build the request, read the response.

Run it with::

    make setup   # in your webrouter clone: every extra
    python examples/custom_provider.py "ada lovelace"
"""

from __future__ import annotations

import re
import sys
from typing import Any, ClassVar, Literal
from urllib.parse import quote

from web_router import WebRouter, register_provider
from web_router.providers import HttpSearchProvider
from web_router.types import SearchRequest, SearchResponse, SearchResult

# Wikipedia marks the matched terms in each snippet with <span> tags.
_TAGS = re.compile(r"<[^>]+>")


class WikipediaProvider(HttpSearchProvider):
    """Wikipedia's MediaWiki search, as a web-router provider."""

    name: ClassVar[str] = "wikipedia"

    # Declare only what you actually map. Anything else a caller sets raises
    # UnsupportedParameterError instead of being quietly ignored -- so start
    # small and widen it as you map more.
    supported_parameters: ClassVar[frozenset[str]] = frozenset({"max_results"})

    base_url: ClassVar[str] = "https://en.wikipedia.org"
    search_path: ClassVar[str] = "/w/api.php"
    http_method: ClassVar[Literal["GET", "POST"]] = "GET"

    # No credential: no auth header is sent, and a missing key never raises.
    env_api_key: ClassVar[str | None] = None

    user_agent: ClassVar[str] = "web-router-example/1.0 (https://github.com/Nimbleway/webrouter)"
    """Wikimedia refuses requests without one, with a 403 naming its policy.

    A worked example of the general case: an API that needs a header beyond
    auth. Set it in ``__init__`` so it travels with the provider, rather than
    making every caller know the policy. See
    https://meta.wikimedia.org/wiki/User-Agent_policy.
    """

    def __init__(self, **kwargs: Any) -> None:
        # Merge rather than replace, so a caller passing other headers keeps the
        # User-Agent, and one setting their own still wins.
        headers = {"User-Agent": self.user_agent, **(kwargs.pop("headers", None) or {})}
        super().__init__(headers=headers, **kwargs)

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        """A normalized request becomes this API's query string."""
        payload: dict[str, Any] = {
            "action": "query",
            "list": "search",
            "format": "json",
            "srsearch": request.query,
        }
        if request.max_results is not None:
            payload["srlimit"] = request.max_results

        # provider_options is the caller's escape hatch; let it win.
        payload.update(request.provider_options)
        return payload

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        """This API's body becomes a normalized response."""
        hits = (payload.get("query") or {}).get("search") or []
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._to_result(hit) for hit in hits],
            # Keep the untouched payload: normalization is never lossy.
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any]) -> SearchResult:
        title = hit.get("title") or ""
        snippet = _TAGS.sub("", hit.get("snippet") or "")
        return SearchResult(
            url=f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
            title=title,
            description=snippet,
            # No fuller text on offer here, so the snippet is the best we have.
            content=snippet,
            published_date=hit.get("timestamp"),
            # Anything unmapped still reaches the caller rather than vanishing.
            extra={k: v for k, v in hit.items() if k not in {"title", "snippet", "timestamp"}},
        )


def main() -> None:
    query = " ".join(sys.argv[1:]) or "ada lovelace"

    # One call, and the name is routable everywhere a built-in name is.
    #
    # A string, not a `Provider` member: `Provider` is a closed enum of the
    # built-ins and cannot be extended, so a backend you add is named by string.
    # That is why every argument naming a provider accepts both.
    register_provider("wikipedia", WikipediaProvider)

    with WebRouter("wikipedia") as router:
        response = router.search(query, max_results=5)

    print(f"provider: {response.provider}  results: {len(response)}\n")
    for index, result in enumerate(response.results, start=1):
        print(f"{index}. {result.title}\n   {result.url}\n   {result.description[:120]}\n")


if __name__ == "__main__":
    main()
