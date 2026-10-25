"""Every configured extract backend on the same URLs, measured -- overall and by the category each URL carries.

The scan next door (``run.py``) asks which search backend finds the answer.
This asks the question for the other half of the package: handed the same
URLs, which extract backend brings the page back, how often, and how fast.
Each URL is one ``benchmark_extract()`` fan-out -- the public ``extract``
path, one call per backend, timed around the call -- and the rows are
measured facts only: whether a document came back, how many characters it
held, the status the backend reported, and what failed. No judge grades the
text, so there is no accuracy column; a page that came back is counted, and
whether its text is complete is a judgment this scan does not make.

    uv run --all-extras python benchmarks/providers/extract.py --limit 100
    uv run --all-extras python benchmarks/providers/extract.py --urls social-pdf
    uv run --all-extras python benchmarks/providers/extract.py --urls mine.csv --url-column url
    uv run --all-extras python benchmarks/providers/extract.py --providers nimble,jina --format html

``--urls`` names a shipped set from ``urls/`` beside this script -- ``general``
(455 URLs) or ``social-pdf`` (911), described in ``urls.py`` -- or a CSV of
your own. Both shipped sets are URL lists sampled from public pages; this
scan carries the URLs and categories only, no ground truth.

Credentials come from the repo's ``.env`` (or the environment): each
backend's own. ``auto`` is left out, as ``benchmark_extract()`` leaves it
out, because it would repeat a member under another name. Sent to every
backend unchanged, so the rows are comparable: the URL and ``--format``.
Unset, ``--format`` lets each backend answer in markdown where it can and
its own default where it cannot (Exa's text); named, a backend that cannot
return it is that row's refusal, never a substitution.

Writes into ``--out``: ``run.json`` (the setup) before the first call,
``rows.jsonl`` (one line per URL and backend) as each URL finishes, and
``report.md`` with ``summary.json``, the same numbers for charts, at the end
-- so a scan that dies keeps the rows it paid for. ``--combine`` merges
finished scans of the same URLs -- backends added later -- into one report
without fetching again. Nothing is estimated, and there is no cost column,
because no backend here states one.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import cli
from _common import MIN_GROUP, Sink, _pct, _wilson, combine_scans, scan_main
from extract_report import markdown
from extract_report import recommended_order as recommended_order
from urls import COMBINE_KEYS, MSG_CANNOT_COMBINE, MSG_OTHER_URLS, load_urls, urls_identity
from urls import URL_SETS as URL_SETS

from web_router import AsyncWebRouter, BenchmarkTimeoutError, ExtractBenchmarkRun
from web_router.constants import BENCHMARK_EXCLUDED
from web_router.exceptions import UnsupportedFormatError
from web_router.registry import configured_extract_providers
from web_router.types import ExtractFormat

# The redundant aliases above re-export what the scan's tests read from this module, its surface.
TIMED_OUT = BenchmarkTimeoutError.__name__
REFUSED = UnsupportedFormatError.__name__


def row_of(url: str, category: str | None, run: ExtractBenchmarkRun) -> dict[str, Any]:
    """One URL on one backend, as measured: what came back, how long it took, or what stopped it.

    A document with no text is ``returned`` but not ``with_text``: the backend
    answered, and what it answered with is the fact recorded. One URL per
    fan-out, so a call that answered carries exactly the one document.
    """
    document = run.response.documents[0] if run.ok and run.response is not None and run.response.documents else None
    chars = len(document.content) if document is not None else 0
    return {
        "url": url,
        "category": category,
        "provider": run.provider,
        "returned": document is not None,
        "with_text": chars > 0,
        "chars": chars,
        "format": document.format if document is not None else None,
        "status_code": document.status_code if document is not None else None,
        "latency": run.latency,
        "error": None if run.ok else str(run.error),
        "error_type": None if run.ok else type(run.error).__name__,
    }


def _score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    returned = [r for r in rows if r["returned"]]
    with_text = [r for r in returned if r["with_text"]]
    latencies = [r["latency"] for r in returned]
    return {
        "urls": len(rows),
        "returned": len(returned),
        "with_text": len(with_text),
        "with_text_rate": len(with_text) / len(rows) if rows else None,
        "with_text_ci95": _wilson(len(with_text), len(rows)),
        "empty": len(returned) - len(with_text),
        "failed": sum(1 for r in rows if not r["returned"] and r["error_type"] not in (TIMED_OUT, REFUSED)),
        "timed_out": sum(1 for r in rows if r["error_type"] == TIMED_OUT),
        "refused": sum(1 for r in rows if r["error_type"] == REFUSED),
        "latency_p50": statistics.median(latencies) if latencies else None,
        "latency_p95": _pct(latencies, 0.95),
        "chars_median": statistics.median([r["chars"] for r in with_text]) if with_text else None,
    }


def _breakdown(rows: list[dict[str, Any]], providers: list[str]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["category"] or "uncategorized"].append(row)
    table = {}
    for group in sorted(groups, key=lambda g: -len(groups[g])):
        members = groups[group]
        scores = {p: _score([r for r in members if r["provider"] == p]) for p in providers}
        urls = len({r["url"] for r in members})
        rates = {p: s["with_text_rate"] for p, s in scores.items() if s["with_text_rate"] is not None}
        most = max((s["with_text"] for s in scores.values()), default=0)
        leaders = [p for p, s in scores.items() if s["with_text"] == most and most] if urls >= MIN_GROUP else []
        table[group] = {"urls": urls, "with_text_rate": rates, "leaders": leaders}
    return table


def summarize(rows: list[dict[str, Any]], setup: dict[str, Any]) -> dict[str, Any]:
    providers = setup["providers"]
    return {
        "setup": setup,
        "overall": {p: _score([r for r in rows if r["provider"] == p]) for p in providers},
        "by_category": _breakdown(rows, providers),
    }


async def run(args: argparse.Namespace, setup: dict[str, Any], sink: Sink) -> list[dict[str, Any]]:
    """Every URL through ``benchmark_extract()``, each URL's rows handed to ``sink`` the moment they exist."""
    urls = load_urls(args)
    setup["asked"] = len(urls)
    providers = setup["providers"]
    print(f"{len(providers)} backends x {len(urls)} URLs -> {args.out}: {', '.join(providers)}", file=sys.stderr)
    limit = asyncio.Semaphore(args.concurrency)
    rows: list[dict[str, Any]] = []
    done = 0

    async with AsyncWebRouter() as router:

        async def one(url: str, category: str | None) -> list[dict[str, Any]]:
            nonlocal done
            async with limit:
                report = await router.benchmark_extract(
                    url, providers=providers, timeout=args.timeout, format=cast(ExtractFormat | None, args.format)
                )
            done += 1
            if done % 10 == 0 or done == len(urls):
                print(f"  {done}/{len(urls)}", file=sys.stderr)
            batch = [row_of(url, category, run) for run in report.runs]
            sink(batch)
            return batch

        for batch in await asyncio.gather(*(one(url, category) for url, category in urls)):
            rows += batch
    return rows


def explicit_providers(text: str) -> list[str]:
    """``--providers`` as a list: trimmed, empty names dropped, repeats kept once in their first position."""
    return list(dict.fromkeys(p.strip() for p in text.split(",") if p.strip()))


def parse_args(argv: list[str]) -> argparse.Namespace:
    """The scan's arguments, described on the command line by this module's first paragraph."""
    return cli.parse_args(argv, description=(__doc__ or "").split("\n\n")[0])


def combine(folders: list[Path]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rows and setup of several scans as one -- say, backends added later on the same URLs."""
    return combine_scans(
        folders,
        keys=COMBINE_KEYS,
        identity=lambda row: row["url"],
        cannot_combine=MSG_CANNOT_COMBINE,
        other_rows=MSG_OTHER_URLS,
    )


def setup_of(args: argparse.Namespace) -> dict[str, Any]:
    """What this scan was asked to do, for ``run.json``: the set, the backends, what was sent and when it started."""
    providers = (
        explicit_providers(args.providers)
        if args.providers
        else [p for p in configured_extract_providers() if p not in BENCHMARK_EXCLUDED]
    )
    return {
        **urls_identity(args),
        "limit": args.limit,
        "seed": args.seed,
        "providers": providers,
        "format": args.format,
        "timeout": args.timeout,
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def main(argv: list[str] | None = None) -> None:
    scan_main(
        argv,
        parse_args=parse_args,
        setup_of=setup_of,
        run=run,
        combine=combine,
        summarize=summarize,
        markdown=markdown,
        out_prefix="extract-",
    )


if __name__ == "__main__":
    main()
