"""Minimal web-router example.

Run it with::

    export NIMBLE_API_KEY=...
    python examples/basic_search.py "who won the 2022 world cup"
"""

from __future__ import annotations

import sys

from web_router import Provider, WebRouter


def main() -> None:
    query = " ".join(sys.argv[1:]) or "what is retrieval augmented generation"

    with WebRouter(Provider.NIMBLE) as router:
        response = router.search(query, max_results=5, search_depth="fast")

    print(f"provider: {response.provider}  results: {len(response)}")
    if response.answer:
        print(f"\nanswer: {response.answer}")

    for index, result in enumerate(response.results, start=1):
        print(f"\n{index}. {result.title}\n   {result.url}\n   {result.description[:160]}")


if __name__ == "__main__":
    main()
