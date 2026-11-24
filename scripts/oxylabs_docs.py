"""Read the targets section of Oxylabs' Web Scraper API docs: its pages, and each source's parameter rows.

The parsing half of ``scripts/oxylabs_classic.py``: :func:`pages` crawls the
docs, :func:`parameters` reads each page's request-parameter tables into the
sources they configure, and :func:`parsed_ecommerce` reads which e-commerce
sources Oxylabs parses.
"""

from __future__ import annotations

import html
import re
from typing import Any

import httpx

DOCS = "https://developers.oxylabs.io/products/web-scraper-api/targets"
SEEDS = ("", "/search-engines/google", "/search-engines/bing", "/video-and-social-media/youtube")
"""Pages the targets index does not link to, so the crawl starts from them as well."""
SEED_PAGES = ("/llms-and-ai/chatgpt", "/llms-and-ai/perplexity", "/llms-and-ai/gemini")
ECOMMERCE = "/e-commerce"
NOT_FOUND = "# Page Not Found"
NAME = re.compile(r"[a-z][a-z0-9_]*[a-z0-9]")
EMPTY = frozenset({"", "-", "\u2013", "\u2014", "marketplace default"})
"""What the docs write in a default cell with no value to send: nothing, a hyphen, an en or em dash, or a note."""
DEFAULT_HEADS = ("default", "default value")
"""A table's default column, by its header: a table without one, its third column a description or an example,
gives each of its parameters no default."""
PARSED_MARK = "✓"


def _text(cell: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()


def _codes(cell: str) -> list[str]:
    return [_text(code) for code in re.findall(r"<code>(.*?)</code>", cell, re.S)]


def _rows(table: str) -> tuple[list[str], list[list[str]]]:
    head = [_text(cell).lower() for cell in re.findall(r"<th[^>]*>(.*?)</th>", table, re.S)]
    rows = [re.findall(r"<td[^>]*>(.*?)</td>", row, re.S) for row in re.findall(r"<tr>(.*?)</tr>", table, re.S)]
    return head, [row for row in rows if row]


def pages(client: httpx.Client) -> dict[str, str]:
    """Every targets page reachable from the seeds, by URL, its body without GitBook's agent notes."""
    queue = [f"{DOCS}{path}.md" for path in (*SEEDS, *SEED_PAGES)]
    pages: dict[str, str] = {}
    seen: set[str] = set()
    link = re.compile(r'(?:href="|\()(' + re.escape(DOCS) + r'/[^")#?\s]+\.md)')
    while queue:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        response = client.get(url)
        body = response.text.split("# Agent Instructions")[0]
        if response.status_code != httpx.codes.OK or NOT_FOUND in body[:600]:
            continue
        pages[url] = body
        queue.extend(found for found in link.findall(body) if found not in seen)
    return pages


def _default_column(head: list[str]) -> int | None:
    """Where a table's defaults are, read from its header; ``None`` for a table with no default column."""
    return next((at for at, cell in enumerate(head) if cell in DEFAULT_HEADS), None)


def _parameter(cells: list[str], default_at: int | None) -> dict[str, Any] | None:
    """One parameter row: its name, whether it is mandatory, its default; ``None`` for a row that is no parameter."""
    words = _text(cells[0]).split()
    if not words:
        return None
    name = re.sub(r"[^\w.:\-]", "", words[0])
    default = _text(cells[default_at]) if default_at is not None and len(cells) > default_at else ""
    return {"name": name, "required": "<mark" in cells[0], "default": None if default in EMPTY else default}


def _source_of(cells: list[str]) -> str | None:
    """The source a ``source`` row sets: its default, or the first name its description quotes."""
    default = _text(cells[2]).strip("`") if len(cells) > 2 else ""
    named = [value for value in (default, *_codes(cells[1])) if NAME.fullmatch(value) and value != "string"]
    return named[0] if named else None


def _table_params(current: dict[str, Any], head: list[str], rows: list[list[str]]) -> None:
    """Add one table's parameters to ``current``, a source's entry; a name seen in an earlier table keeps that row."""
    default_at = _default_column(head)
    for row in rows:
        parameter = _parameter(row, default_at) if len(row) > 1 else None
        if parameter and parameter["name"] != "source":
            current["params"].setdefault(parameter["name"], parameter)


def parameters(pages: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Each source's docs page and parameters; a table with no ``source`` row adds to the page's last source."""
    found: dict[str, dict[str, Any]] = {}
    for url, body in pages.items():
        current: dict[str, Any] | None = None
        for table in re.findall(r"<table.*?</table>", body, re.S):
            head, rows = _rows(table)
            if not head or head[0] != "parameter":
                continue
            source = next((_source_of(row) for row in rows if _text(row[0]) == "source" and len(row) > 1), None)
            if source:
                current = found.setdefault(source, {"docs": url.removesuffix(".md"), "params": {}})
            if current is not None:
                _table_params(current, head, rows)
    return found


def parsed_ecommerce(pages: dict[str, str]) -> set[str]:
    """The e-commerce sources its table marks as parsed. A target's later rows drop the target column."""
    parsed: set[str] = set()
    body = pages.get(f"{DOCS}{ECOMMERCE}.md", "")
    for table in re.findall(r"<table.*?</table>", body, re.S):
        head, rows = _rows(table)
        if "source" not in head:
            continue
        for row in rows:
            cells = [_text(cell) for cell in row]
            at = 0 if cells and NAME.fullmatch(cells[0]) else 1
            if len(cells) > at + 1 and NAME.fullmatch(cells[at]) and PARSED_MARK in cells[at + 1]:
                parsed.add(cells[at])
    return parsed
