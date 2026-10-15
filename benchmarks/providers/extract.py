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

``--urls`` names a set from ``urls/`` beside this script -- ``general`` (455
URLs: reference sites, dev docs, news, government, PDFs, social, a long
tail) or ``social-pdf`` (918 URLs stratified into social, PDF and long-tail
pages) -- or a CSV of your own with a URL column and, optionally, a category
column to break the numbers down by. Both shipped sets are URL lists sampled
from anonymized third-party evaluations of extraction APIs, kept in
``Nimbleway/extract-benchmarks``; this scan carries the URLs and categories
only, no ground truth.

Credentials come from the repo's ``.env`` (or the environment): each
backend's own. ``auto`` is left out, as ``benchmark_extract()`` leaves it
out, because it would repeat a member under another name. Sent to every
backend unchanged, so the rows are comparable: the URL and ``--format``.
Unset, ``--format`` lets each backend answer in markdown where it can and
its own default where it cannot (Exa's text); named, a backend that cannot
return it is that row's refusal, never a substitution.

Writes into ``--out``: ``run.json`` (the setup), ``rows.jsonl`` (one line
per URL and backend) and ``report.md`` with ``summary.json``, the same
numbers for charts. ``--combine`` merges finished scans of the same URLs --
backends added later -- into one report without fetching again. Nothing is
estimated, and there is no cost column, because no backend here states one.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv

from web_router import AsyncWebRouter, BenchmarkTimeoutError, ExtractBenchmarkRun
from web_router.constants import BENCHMARK_EXCLUDED, BENCHMARK_EXTRACT_TIMEOUT, PROVIDER_AUTO
from web_router.exceptions import UnsupportedFormatError
from web_router.registry import configured_extract_providers
from web_router.types import ExtractFormat

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"
DEFAULT_OUT = Path(__file__).resolve().parent / "results"
URLS_DIR = Path(__file__).resolve().parent / "urls"
URL_SETS: dict[str, dict[str, Any]] = {
    "general": {
        "file": "general-455.csv",
        "title": "the general mix",
        "description": (
            "455 public URLs: reference sites (Wikipedia in several languages, arXiv, GitHub, MDN, PyPI, SEC), "
            "news in six languages, government and legal pages, social networks, 60 PDFs and a long tail of small "
            "sites, at most 8 per domain. Sampled from anonymized third-party evaluations of extraction APIs."
        ),
    },
    "social-pdf": {
        "file": "social-pdf-918.csv",
        "title": "the social / PDF / long-tail set",
        "description": (
            "918 public URLs across 578 domains, stratified into social and UGC pages (131), PDF documents (409) "
            "and a long tail of general pages (378), with a strong non-English share. Sampled from anonymized "
            "third-party evaluations of extraction APIs."
        ),
    },
}
"""``--urls``: the shipped sets, each a CSV of ``url,category`` under ``urls/``."""
DEFAULT_SET = "general"
DEFAULT_LIMIT = 100
DEFAULT_CONCURRENCY = 4
"""URLs in flight at once; each fans out to every backend, and each backend's own extract limits itself."""
MIN_GROUP = 10
"""A category with fewer URLs than this is shown but never names any backend's best."""
Z_95 = 1.96
FORMATS = ("markdown", "html", "text")
MSG_OUT_NOT_EMPTY = "{path} already exists; pass a new --out folder rather than mixing two runs in one."
MSG_NO_URL_COLUMN = "{path} has no {column!r} column; its columns are {columns}. Name the right one with --url-column."
MSG_NO_URLS = "{path} holds no URLs after --limit {limit} and deduplication."
MSG_UNKNOWN_SET = "--urls {name!r}: not a shipped set ({known}) and not a file."
MSG_OTHER_URLS = "{folder} fetched different URLs from the first scan; only scans of the same URLs combine"
MSG_REPEATED_BACKEND = "{folder} has {providers} again; each backend may appear in only one of the scans combined"
MSG_CANNOT_COMBINE = (
    "{folder} was a different run ({key}: {theirs!r}, not {ours!r}); only scans of the same URLs, format and "
    "timeout combine"
)
COMBINE_KEYS = ("urls", "urls_sha256", "url_column", "category_column", "limit", "seed", "format", "timeout")
"""What two scans must share for their rows to sit in one table: the same URLs, sent the same way."""
APART = {None: "best", False: "not shown apart", True: "behind"}
"""The recommended order's last column: whether this run tells a backend from the best one."""
TIMED_OUT = BenchmarkTimeoutError.__name__
REFUSED = UnsupportedFormatError.__name__


def _wilson(hits: int, n: int) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = hits / n
    denominator = 1 + Z_95**2 / n
    centre = (p + Z_95**2 / (2 * n)) / denominator
    half = Z_95 * math.sqrt(p * (1 - p) / n + Z_95**2 / (4 * n * n)) / denominator
    return (centre - half, centre + half)


def _pct(values: list[float], fraction: float) -> float | None:
    # Nearest rank, as the search scan computes its p95, so the two read alike.
    values = sorted(values)
    return values[max(0, min(len(values) - 1, math.ceil(fraction * len(values)) - 1))] if values else None


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


def recommended_order(summary: dict[str, Any]) -> list[tuple[str, bool | None]]:
    """The backends by pages brought back with text, out of every URL asked, each with whether this run shows it behind.

    Ranked on pages with text over every URL, a failure or a timeout counting
    as a miss, as the search scan ranks on correct answers over every
    question. ``auto`` is left out if a scan named it: as a chain member it
    would be a second ``auto`` on the default order. So is a backend that
    returned nothing at all. A backend is behind only when its 95% interval
    stops short of the best one's; ``None`` marks the best itself.
    """
    overall = summary["overall"]
    plain = [p for p in summary["setup"]["providers"] if p != PROVIDER_AUTO and overall[p]["returned"]]
    ranked = sorted(plain, key=lambda p: -(overall[p]["with_text_rate"] or 0.0))
    if not ranked:
        return []
    floor = (overall[ranked[0]]["with_text_ci95"] or (0.0, 0.0))[0]
    return [(ranked[0], None)] + [(p, (overall[p]["with_text_ci95"] or (0.0, 0.0))[1] < floor) for p in ranked[1:]]


def _recommendation(summary: dict[str, Any]) -> list[str]:
    order = recommended_order(summary)
    lines = ["## Recommended auto order\n"]
    if not order:
        lines.append("No backend in this scan brought a page back, so there is no order to suggest.\n")
        return lines
    overall = summary["overall"]
    lines.append(
        f"A suggestion from one scan of {summary['setup']['urls_title']}, not the library's default: the backends by "
        "pages brought back with text out of every URL asked, a failure or a timeout counting as a miss. It says "
        "which backend returns a page, not whose page is best. A backend is *behind* only when its 95% interval "
        "stops short of the best one's; the others are not shown apart by this run, and another could order them "
        "differently.\n"
    )
    lines.append(
        "| Order | Backend | Pages with text of all URLs | 95% CI | Empty | Failed | Timed out | Against the best |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for rank, (p, behind) in enumerate(order, 1):
        s = overall[p]
        ci = s["with_text_ci95"] or (0.0, 0.0)
        lines.append(
            f"| {rank} | `{p}` | {_p(s['with_text_rate'])} ({s['with_text']}/{s['urls']}) | {_p(ci[0])}-{_p(ci[1])} "
            f"| {s['empty']} | {s['failed']} | {s['timed_out']} | {APART[behind]} |"
        )
    chain = json.dumps({"auto": {"chain": {"extract": [p for p, _ in order]}}})
    lines.append(
        "\nIt is opt-in: to try it, give your own router the order. The chain replaces the default one, keyless "
        "tail included, and each member still needs its credentials; keyed by `extract`, it leaves the other "
        "methods' chains alone.\n"
    )
    lines.append(f"```python\nWebRouter(provider_config={chain})\n```\n")
    lines.append("The library's default order changes only by a pull request backed by a scan like this one.\n")
    return lines


def _p(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _s(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}s"


def _k(value: float | None) -> str:
    return "n/a" if value is None else f"{value / 1000:.1f}k"


def markdown(summary: dict[str, Any]) -> str:
    setup, overall = summary["setup"], summary["overall"]
    providers = sorted(setup["providers"], key=lambda p: -(overall[p]["with_text_rate"] or 0))
    out: list[str] = []
    w = out.append
    w(f"# Extract backends on {setup['urls_title']}: pages returned by backend and by category\n")
    sent = f"`format={setup['format']}`" if setup.get("format") else "no format named (markdown where a backend can)"
    w(
        f"{setup.get('asked', setup['limit'])} URLs of {setup['urls_title']} (seed {setup['seed']}), one extract per "
        f"backend with {sent} and {setup['timeout']:g}s for each URL's comparison. A page is *returned* when the call "
        "answered with a document, and *with text* when that document had any content; nothing judges the text. "
        f"Started {setup['started']}.\n"
    )
    w("## Overall\n")
    w(
        "| Backend | Pages with text | 95% CI | Empty | Failed | Timed out | Refused | p50 latency | p95 latency "
        "| Median chars |"
    )
    w("|---|---|---|---|---|---|---|---|---|---|")
    for p in providers:
        s = overall[p]
        ci = s["with_text_ci95"]
        w(
            f"| `{p}` | {_p(s['with_text_rate'])} ({s['with_text']}/{s['urls']}) "
            f"| {_p(ci[0]) + '-' + _p(ci[1]) if ci else 'n/a'} | {s['empty']} | {s['failed']} | {s['timed_out']} "
            f"| {s['refused']} | {_s(s['latency_p50'])} | {_s(s['latency_p95'])} | {_k(s['chars_median'])} |"
        )
    w("")
    w(
        "*Empty* is a document that came back with no text; *failed* a call the backend refused or could not "
        "complete; *timed out* a call still unanswered when the comparison's timeout ran out; *refused* a format "
        "the backend cannot return. Latency is over the calls that answered.\n"
    )
    out.extend(_recommendation(summary))
    w("## By category\n")
    w(
        f"Pages with text per backend over the URLs in the category; **bold** marks the most pages brought back with "
        f"text in the row. Rows under {MIN_GROUP} URLs name no best -- too few to tell backends apart.\n"
    )
    w("| Category | URLs | " + " | ".join(f"`{p}`" for p in providers) + " |")
    w("|---|---|" + "---|" * len(providers))
    for group, entry in summary["by_category"].items():
        cells = []
        for p in providers:
            cell = _p(entry["with_text_rate"].get(p))
            cells.append(f"**{cell}**" if p in entry["leaders"] else cell)
        w(f"| {group} | {entry['urls']} | " + " | ".join(cells) + " |")
    w("")
    w("## How to read this\n")
    w(
        "- **A returned page is not a correct page.** A backend that answers with a bot wall, a cookie banner or a "
        "login page has returned a document with text; this scan counts it. Judging the text against the page is "
        "work for a scorer with ground truth, which this scan does not carry.\n"
        "- **Intervals are wide.** Two backends whose 95% intervals overlap are not shown apart by this run; a "
        "category row with a few dozen URLs is wider still.\n"
        "- **Latency is around one call for one URL**, through the public `extract` path, with every backend "
        "called at once. A backend's own rate limit shows up here as latency or as failures.\n"
        "- **No cost column.** No backend states a price in its response; read these against `PRICING.md`."
    )
    return "\n".join(out) + "\n"


def resolve_urls(name: str) -> tuple[Path, dict[str, Any]]:
    """``--urls`` as the CSV to read and what the report calls it: a shipped set by name, or a file of your own."""
    if name in URL_SETS:
        chosen = URL_SETS[name]
        return URLS_DIR / chosen["file"], {"urls": name, "urls_title": chosen["title"]}
    path = Path(name)
    if not path.is_file():
        raise SystemExit(MSG_UNKNOWN_SET.format(name=name, known=", ".join(URL_SETS)))
    return path, {"urls": "csv", "urls_title": f"`{path.name}`"}


def load_urls(args: argparse.Namespace) -> list[tuple[str, str | None]]:
    """The URLs to fetch with their categories, deduplicated, sampled by ``--seed`` down to ``--limit``."""
    path, _ = resolve_urls(args.urls)
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        if args.url_column not in columns:
            raise SystemExit(MSG_NO_URL_COLUMN.format(path=path, column=args.url_column, columns=columns))
        seen: set[str] = set()
        rows: list[tuple[str, str | None]] = []
        for record in reader:
            url = (record.get(args.url_column) or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            category = (record.get(args.category_column) or "").strip() or None
            rows.append((url, category))
    if args.limit is not None and args.limit < len(rows):
        # The same seed picks the same URLs, so two scans can be combined; a limit past the set is the whole set.
        rows = sorted(random.Random(args.seed).sample(rows, args.limit), key=rows.index)
    if not rows:
        raise SystemExit(MSG_NO_URLS.format(path=path, limit=args.limit))
    return rows


def urls_identity(args: argparse.Namespace) -> dict[str, Any]:
    """The set's digest and columns, for ``run.json``: two scans combine only on the very same file."""
    path, named = resolve_urls(args.urls)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {**named, "urls_sha256": digest, "url_column": args.url_column, "category_column": args.category_column}


async def run(args: argparse.Namespace, setup: dict[str, Any]) -> list[dict[str, Any]]:
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
            return [row_of(url, category, run) for run in report.runs]

        for batch in await asyncio.gather(*(one(url, category) for url, category in urls)):
            rows += batch
    return rows


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument(
        "--urls", default=DEFAULT_SET, help=f"a shipped set ({', '.join(URL_SETS)}) or a CSV (default: {DEFAULT_SET})"
    )
    parser.add_argument("--url-column", default="url", help="the URL column of --urls")
    parser.add_argument("--category-column", default="category", help="the column to break results down by")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="URLs to sample (0 for the whole set)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--providers", default=None, help="comma-separated (default: every configured backend)")
    parser.add_argument("--format", choices=FORMATS, default=None, help="sent to every backend (default: unset)")
    parser.add_argument(
        "--timeout", type=float, default=BENCHMARK_EXTRACT_TIMEOUT, help="seconds for each URL's comparison"
    )
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="URLs at once")
    parser.add_argument("--out", type=Path, default=None, help=f"results folder (default: under {DEFAULT_OUT})")
    parser.add_argument(
        "--combine",
        type=Path,
        nargs="+",
        default=None,
        help="merge finished scans of the same URLs into --out, fetching nothing",
    )
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        args.limit = None
    return args


def combine(folders: list[Path]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rows and setup of several scans as one -- say, backends added later on the same URLs."""
    rows: list[dict[str, Any]] = []
    setup: dict[str, Any] = {}
    for folder in folders:
        theirs = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        if not setup:
            setup = {**theirs, "providers": [], "combined_from": []}
        for key in COMBINE_KEYS:
            if theirs.get(key) != setup.get(key):
                raise SystemExit(
                    MSG_CANNOT_COMBINE.format(folder=folder, key=key, theirs=theirs.get(key), ours=setup.get(key))
                )
        repeated = [p for p in theirs["providers"] if p in setup["providers"]]
        if repeated:
            raise SystemExit(MSG_REPEATED_BACKEND.format(folder=folder, providers=", ".join(repeated)))
        theirs_rows = [
            json.loads(line) for line in (folder / "rows.jsonl").read_text(encoding="utf-8").splitlines() if line
        ]
        fetched = {r["url"] for r in theirs_rows}
        if rows and fetched != {r["url"] for r in rows}:
            raise SystemExit(MSG_OTHER_URLS.format(folder=folder))
        setup["providers"] += theirs["providers"]
        setup["combined_from"].append(str(folder))
        rows += theirs_rows
    return rows, setup


def main(argv: list[str] | None = None) -> None:
    # An entry point, like the search scan's: it may read the repo's .env,
    # and a variable already exported wins. The library itself never does.
    if ENV_FILE.is_file():
        load_dotenv(ENV_FILE)
    args = parse_args(sys.argv[1:] if argv is None else argv)
    args.out = args.out or DEFAULT_OUT / ("extract-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    if (args.out / "rows.jsonl").exists():
        raise SystemExit(MSG_OUT_NOT_EMPTY.format(path=args.out / "rows.jsonl"))
    args.out.mkdir(parents=True, exist_ok=True)
    if args.combine:
        _write(args.out, *combine(args.combine))
        return
    providers = (
        [p.strip() for p in args.providers.split(",") if p.strip()]
        if args.providers
        else [p for p in configured_extract_providers() if p not in BENCHMARK_EXCLUDED]
    )
    setup: dict[str, Any] = {
        **urls_identity(args),
        "limit": args.limit,
        "seed": args.seed,
        "providers": providers,
        "format": args.format,
        "timeout": args.timeout,
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    rows = asyncio.run(run(args, setup))
    _write(args.out, rows, setup)


def _write(out: Path, rows: list[dict[str, Any]], setup: dict[str, Any]) -> None:
    (out / "run.json").write_text(json.dumps(setup, indent=2), encoding="utf-8")
    (out / "rows.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    summary = summarize(rows, setup)
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report = markdown(summary)
    (out / "report.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
