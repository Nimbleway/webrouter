"""Regenerate the price tables in ``benchmarks/providers/PRICING.md`` from ``web_router/data/prices.json``.

Each table sits between a pair of markers naming the rows it shows; the prose
around them is hand-written and left alone.

    <!-- prices: method=search provider=you,you-mcp -->
    ...generated table...
    <!-- /prices -->

    make prices                                 # rewrite the tables
    uv run python -m scripts.prices --check     # exit 1 if a table is stale
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from web_router.prices import Price, prices

ROOT = Path(__file__).resolve().parent.parent
PRICING_MD = ROOT / "benchmarks" / "providers" / "PRICING.md"

BLOCK = re.compile(r"(?P<open><!-- prices: (?P<spec>[^>]*?) -->\n)(?P<body>.*?)(?P<close><!-- /prices -->)", re.DOTALL)

UNIT_TEXT = {
    "per_request": "per request",
    "per_1k": "per 1k",
    "per_page": "per page",
    "per_run": "per run",
}

MINUTES_PER_HOUR = 60

EMPTY = "—"


def _usd(value: float) -> str:
    if value == int(value):
        return f"${int(value)}"
    text = f"{value:.6f}".rstrip("0")
    decimals = len(text.split(".")[1])
    return f"${value:.{max(decimals, 2)}f}"


def _price_text(row: Price) -> str:
    if row.price_usd is None:
        base = "billed by tokens" if row.unit == "token_billed" else ""
    elif row.price_usd == 0:
        base = "free"
    elif row.unit == "per_browser_minute":
        base = f"{_usd(round(row.price_usd * MINUTES_PER_HOUR, 4))} an hour"
    else:
        base = f"{_usd(row.price_usd)} {UNIT_TEXT[row.unit]}"
    parts = [part for part in (base, row.note) if part]
    return "; ".join(parts) if parts else "not published"


def _mode_text(row: Price) -> str:
    if row.mode is None:
        return "default"
    return f"`{row.mode}` (default)" if row.default else f"`{row.mode}`"


def _source_text(row: Price) -> str:
    if row.source_url is None:
        return EMPTY
    return f"[{urlparse(row.source_url).netloc}]({row.source_url})"


COLUMNS: tuple[tuple[str, Callable[[Price], str | None]], ...] = (
    ("Backend", lambda row: row.provider),
    ("Mode", _mode_text),
    ("Setup", lambda row: row.setup),
    ("What web-router sends", lambda row: row.setting),
    ("What it returns", lambda row: row.does),
    ("Estimated price", _price_text),
    ("Source", _source_text),
    ("Checked", lambda row: row.checked.isoformat() if row.checked else None),
)


def _parse(spec: str) -> dict[str, list[str]]:
    fields = dict(part.split("=", 1) for part in spec.split())
    return {name: value.split(",") for name, value in fields.items()}


def select(spec: str, rows: tuple[Price, ...]) -> list[Price]:
    """The rows a block's marker names, in file order."""
    wanted = _parse(spec)
    return [
        row
        for row in rows
        if row.method in wanted["method"] and ("provider" not in wanted or row.provider in wanted["provider"])
    ]


def _cell(value: str | None) -> str:
    return EMPTY if not value else value.replace("|", "\\|")


def render(rows: list[Price]) -> str:
    """One Markdown table, leaving out a column with nothing to say: empty in every row, or one backend or mode."""
    dropped = set()
    if len({row.provider for row in rows}) == 1:
        dropped.add("Backend")
    if all(row.mode is None for row in rows):
        dropped.add("Mode")
    columns = [(title, get) for title, get in COLUMNS if title not in dropped and any(get(row) for row in rows)]
    lines = [
        "| " + " | ".join(title for title, _ in columns) + " |",
        "|" + "---|" * len(columns),
        *("| " + " | ".join(_cell(get(row)) for _, get in columns) + " |" for row in rows),
    ]
    return "\n".join(lines) + "\n"


def regenerate(text: str, rows: tuple[Price, ...]) -> str:
    def replace(match: re.Match[str]) -> str:
        return match["open"] + render(select(match["spec"], rows)) + match["close"]

    return BLOCK.sub(replace, text)


def specs(text: str) -> list[str]:
    return [match["spec"] for match in BLOCK.finditer(text)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit 1 if PRICING.md's tables are stale")
    args = parser.parse_args(argv)
    current = PRICING_MD.read_text(encoding="utf-8")
    fresh = regenerate(current, prices())
    if args.check:
        if fresh != current:
            print("PRICING.md's tables are stale: run `make prices`.", file=sys.stderr)
            return 1
        return 0
    PRICING_MD.write_text(fresh, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
