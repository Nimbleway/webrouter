"""Regenerate ``web_router/data/oxylabs_classic_sources.json``: the Oxylabs Web Scraper API's parsed sources.

The Web Scraper API (Oxylabs' username-and-password API, which its docs now call
"Classic") has no endpoint that lists its sources, unlike the Web API's
``GET /v1/scrapers``. So the list ships with the library, read from the
targets section of Oxylabs' docs: every page's request-parameter tables name
the ``source`` they configure, which parameters it takes, which of them are
mandatory, and which sit under ``context``. Only names, flags and defaults are
kept; no description text is copied.

A source joins only if Oxylabs parses it. Live data answers with records, and
a source with no dedicated parser answers ``parse: true`` with
``parse_status_code`` 12003 and no fields. The e-commerce table marks each
source's parser. Elsewhere a source is parsed when its docs list ``parse``;
one that does not answers in JSON of its own and refuses ``parse``, so
``parse`` records whether it is sent. :data:`LEFT_OUT` names the rest.

    make oxylabs-classic                                # rewrite the file
    uv run python -m scripts.oxylabs_classic --check    # exit 1 if it is stale
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "web_router" / "data" / "oxylabs_classic_sources.json"

DOCS = "https://developers.oxylabs.io/products/web-scraper-api/targets"
SEEDS = ("", "/search-engines/google", "/search-engines/bing", "/video-and-social-media/youtube")
"""Pages the targets index does not link to, so the crawl starts from them as well."""
SEED_PAGES = ("/llms-and-ai/chatgpt", "/llms-and-ai/perplexity", "/llms-and-ai/gemini")
ECOMMERCE = "/e-commerce"

LEFT_OUT = frozenset(
    {"universal", "google", "bing", "youtube_download", "google_maps", "youtube_autocomplete"}
    | {"chatgpt", "perplexity", "gemini"}
)
"""Page fetches by URL, which ``extract`` covers; a download delivered to the caller's own bucket; two sources that
refuse ``parse`` and answer with a page or with JavaScript rather than JSON; and the LLM sources, which the realtime
endpoint refuses (HTTP 422) as they run only as push-pull jobs. All seen live on 2026-11-17."""
TRANSPORT = frozenset({"source", "parse", "callback_url", "storage_type", "storage_url"})
"""Set by the backend or never sent: what makes a run a parsed, synchronous answer."""
RENDERED = "html"
"""The value a source that requires ``render`` is sent: its docs' only one."""
CONTEXT_PREFIX = "context:"
NOT_FOUND = "# Page Not Found"
NAME = re.compile(r"[a-z][a-z0-9_]*[a-z0-9]")
EMPTY = frozenset({"", "-", "\u2013", "\u2014"})
"""What the docs write in an empty default cell: nothing, a hyphen, or an en or em dash."""
PARSED_MARK = "✓"


def _text(cell: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()


def _codes(cell: str) -> list[str]:
    return [_text(code) for code in re.findall(r"<code>(.*?)</code>", cell, re.S)]


def _rows(table: str) -> tuple[list[str], list[list[str]]]:
    head = [_text(cell).lower() for cell in re.findall(r"<th[^>]*>(.*?)</th>", table, re.S)]
    rows = [re.findall(r"<td[^>]*>(.*?)</td>", row, re.S) for row in re.findall(r"<tr>(.*?)</tr>", table, re.S)]
    return head, [row for row in rows if row]


def _pages(client: httpx.Client) -> dict[str, str]:
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


def _parameter(cells: list[str]) -> dict[str, Any] | None:
    """One parameter row: its name, whether it is mandatory, its default; ``None`` for a row that is no parameter."""
    words = _text(cells[0]).split()
    if not words:
        return None
    name = re.sub(r"[^\w.:\-]", "", words[0])
    default = _text(cells[2]) if len(cells) > 2 else ""
    return {"name": name, "required": "<mark" in cells[0], "default": None if default in EMPTY else default}


def _source_of(cells: list[str]) -> str | None:
    """The source a ``source`` row sets: its default, or the first name its description quotes."""
    default = _text(cells[2]).strip("`") if len(cells) > 2 else ""
    named = [value for value in (default, *_codes(cells[1])) if NAME.fullmatch(value) and value != "string"]
    return named[0] if named else None


def _parameters(pages: dict[str, str]) -> dict[str, dict[str, Any]]:
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
            if current is None:
                continue
            for row in rows:
                parameter = _parameter(row) if len(row) > 1 else None
                if parameter and parameter["name"] != "source":
                    current["params"].setdefault(parameter["name"], parameter)
    return found


def _parsed_ecommerce(pages: dict[str, str]) -> set[str]:
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


def _entry(name: str, found: dict[str, Any]) -> dict[str, Any]:
    """One source as the library reads it: its inputs, those under ``context``, and the values always sent."""
    params = found["params"]
    inputs: dict[str, Any] = {}
    context: list[str] = []
    fixed: dict[str, str] = {}
    for raw, parameter in params.items():
        key = raw.removeprefix(CONTEXT_PREFIX)
        if raw.startswith(CONTEXT_PREFIX):
            if not key:
                continue
            context.append(key)
        if key in TRANSPORT:
            continue
        if key == "render" and parameter["required"]:
            fixed["render"] = RENDERED
            continue
        inputs[key] = {"required": parameter["required"], "default": parameter["default"]}
    category, _, rest = found["docs"].removeprefix(DOCS + "/").partition("/")
    site = rest.split("/")[0] if rest else name
    parse = category == ECOMMERCE.strip("/") or "parse" in params
    entry: dict[str, Any] = {"name": name, "site": site, "category": category, "docs": found["docs"], "parse": parse}
    entry |= {"inputs": inputs, "context": sorted(context), "fixed": fixed}
    return entry


def build() -> dict[str, Any]:
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        pages = _pages(client)
    found = _parameters(pages)
    ecommerce = f"{DOCS}{ECOMMERCE}/"
    parsed = _parsed_ecommerce(pages)
    keep = [
        name
        for name, entry in found.items()
        if name not in LEFT_OUT and (not entry["docs"].startswith(ecommerce) or name in parsed)
    ]
    sources = [_entry(name, found[name]) for name in sorted(keep)]
    return {"docs": DOCS, "sources": sources}


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the committed file differs")
    args = parser.parse_args()
    text = render(build())
    if args.check:
        if OUT.read_text() != text:
            print(f"{OUT.relative_to(ROOT)} is stale: run make oxylabs-classic", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text)
    count = len(json.loads(text)["sources"])
    print(f"wrote {OUT.relative_to(ROOT)}: {count} sources", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
