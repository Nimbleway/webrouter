"""Fetch one URL through every usable extract backend at once and compare what comes back.

Set the keys for whichever providers you want to include::

    export NIMBLE_API_KEY=... TAVILY_API_KEY=... FIRECRAWL_API_KEY=...
    python examples/compare_extract_providers.py https://example.com

Providers with no key are skipped. With no keys at all it still runs, against
Jina Reader, which needs none.
"""

from __future__ import annotations

import asyncio
import sys

from web_router import AsyncWebRouter

TIMEOUT = 60.0
"""Seconds for the whole comparison: a backend still silent by then gets a timed-out row, not the report."""


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"

    # With `providers` unset, benchmark_extract() compares every extract
    # backend this environment can use -- the ones with credentials plus the
    # keyless tier -- less `auto`, which would only repeat one of them. No
    # format is named, so each backend answers in markdown where it can and
    # its own default where it cannot, and says which in `document.format`.
    async with AsyncWebRouter() as router:
        report = await router.benchmark_extract(url, timeout=TIMEOUT)

    print(f"comparing: {', '.join(run.provider for run in report.runs)}")

    for run in report.runs:
        if not run.ok:
            # One provider being down, keyless, or unable to return the format
            # is its own row, so it never hides the rest of the comparison.
            print(f"\n{run.provider}  unavailable: {str(run.error)[:80]}")
            continue
        assert run.response is not None
        print(f"\n{run.provider}  {run.latency * 1000:.0f} ms  ({run.count} documents)")
        for document in run.response.documents:
            print(f"  {document.url}")
            print(f"    {len(document.content):>6} chars of {document.format}  status {document.status_code}")
        for failure in run.response.failures:
            print(f"  {failure.url}  failed: {failure.error[:60]}")


if __name__ == "__main__":
    asyncio.run(main())
