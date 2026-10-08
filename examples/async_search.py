"""The same search, run concurrently through the async router.

Run it with::

    export NIMBLE_API_KEY=...
    python examples/async_search.py
"""

from __future__ import annotations

import asyncio

from web_router import AsyncWebRouter, Provider

QUERIES = [
    "latest nvidia earnings",
    "what is retrieval augmented generation",
    "python 3.13 release notes",
]


async def main() -> None:
    async with AsyncWebRouter(Provider.NIMBLE) as router:
        responses = await asyncio.gather(*(router.search(query, max_results=3) for query in QUERIES))

    for response in responses:
        print(f"\n{response.query} -> {len(response)} results")
        for result in response.results:
            print(f"  - {result.url}")


if __name__ == "__main__":
    asyncio.run(main())
