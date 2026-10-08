"""Run one query through every usable search backend at once and compare what comes back.

Set the keys for whichever providers you want to include::

    export NIMBLE_API_KEY=... TAVILY_API_KEY=... YOU_API_KEY=...
    python examples/compare_providers.py "what is retrieval augmented generation"

Providers with no key are skipped. With no keys at all it still runs, against
the ones that need none.
"""

from __future__ import annotations

import asyncio
import sys

from web_router import AsyncWebRouter

TIMEOUT = 20.0
"""Seconds for the whole comparison: a backend still silent by then gets a timed-out row, not the report."""


async def main() -> None:
    query = " ".join(sys.argv[1:]) or "what is retrieval augmented generation"

    # With `providers` unset, benchmark() compares every backend this
    # environment can use -- the ones with a key plus the keyless tier -- less
    # `auto`, which would only repeat one of them.
    async with AsyncWebRouter() as router:
        report = await router.benchmark(query, max_results=3, timeout=TIMEOUT)

    print(f"comparing: {', '.join(run.provider for run in report.runs)}")

    for run in report.runs:
        if not run.ok:
            # One provider being down, keyless, or refusing an option is its
            # own row, so it never hides the rest of the comparison.
            print(f"\n{run.provider}  unavailable: {str(run.error)[:80]}")
            continue
        assert run.response is not None
        print(f"\n{run.provider}  {run.latency * 1000:.0f} ms  ({run.count} results)")
        for result in run.response.results:
            print(f"  {result.url}")
            print(f"    {len(result.content):>6} chars of content")


if __name__ == "__main__":
    asyncio.run(main())
