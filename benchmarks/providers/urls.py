"""The URLs an extract scan fetches: the shipped sets, a CSV of your own, and what two scans must share to combine.

``--urls`` names a set from ``urls/`` beside this module -- ``general`` or
``social-pdf``, each a CSV of ``url,category`` -- or a CSV of your own with
a URL column and, optionally, a category column to break the numbers down
by. Loading deduplicates and samples by ``--seed`` down to ``--limit``, so
two scans on the same seed fetch the same URLs and their rows can sit in
one table.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import random
from pathlib import Path
from typing import Any

URLS_DIR = Path(__file__).resolve().parent / "urls"
URL_SETS: dict[str, dict[str, Any]] = {
    "general": {
        "file": "general-455.csv",
        "title": "the general mix",
        "description": (
            "455 public URLs: reference sites (Wikipedia in several languages, arXiv, GitHub, MDN, PyPI, SEC), "
            "news in six languages, government and legal pages, social networks, 60 PDFs and a long tail of small "
            "sites, at most 8 per domain. Sampled from public pages."
        ),
    },
    "social-pdf": {
        "file": "social-pdf-911.csv",
        "title": "the social / PDF / long-tail set",
        "description": (
            "911 public URLs across 576 domains, stratified into social and UGC pages (131), PDF documents (407) "
            "and a long tail of general pages (373), with a strong non-English share. Sampled from public pages."
        ),
    },
}
"""``--urls``: the shipped sets, each a CSV of ``url,category`` under ``urls/``."""
MSG_NO_URL_COLUMN = "{path} has no {column!r} column; its columns are {columns}. Name the right one with --url-column."
MSG_NO_URLS = "{path} holds no URLs after --limit {limit} and deduplication."
MSG_UNKNOWN_SET = "--urls {name!r}: not a shipped set ({known}) and not a file."
COMBINE_KEYS = ("urls", "urls_sha256", "url_column", "category_column", "limit", "seed", "format", "timeout")
"""What two scans must share for their rows to sit in one table: the same URLs, sent the same way."""
MSG_OTHER_URLS = "{folder} fetched different URLs from the first scan; only scans of the same URLs combine"
MSG_CANNOT_COMBINE = (
    "{folder} was a different run ({key}: {theirs!r}, not {ours!r}); only scans of the same URLs, format and "
    "timeout combine"
)


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
