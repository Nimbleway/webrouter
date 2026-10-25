"""The extract scan's command line: its arguments, their defaults, and what each must be."""

from __future__ import annotations

import argparse
from pathlib import Path

from _common import DEFAULT_OUT
from urls import URL_SETS

from web_router.constants import BENCHMARK_EXTRACT_TIMEOUT

DEFAULT_SET = "general"
DEFAULT_LIMIT = 100
DEFAULT_CONCURRENCY = 4
"""URLs in flight at once; each fans out to every backend, and each backend's own extract limits itself."""
FORMATS = ("markdown", "html", "text")


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {value}")
    return value


def parse_args(argv: list[str], *, description: str) -> argparse.Namespace:
    """The scan's arguments; ``--limit 0`` is the whole set, read back as no limit."""
    parser = argparse.ArgumentParser(description=description)
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
    parser.add_argument(
        "--concurrency", type=_positive_int, default=DEFAULT_CONCURRENCY, help="URLs at once (at least 1)"
    )
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
