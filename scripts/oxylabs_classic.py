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
import json
import sys
from pathlib import Path
from typing import Any

import httpx

from scripts.oxylabs_docs import DOCS, ECOMMERCE, pages, parameters, parsed_ecommerce
from web_router.constants_oxylabs_classic import OXYLABS_CLASSIC_TRANSPORT_PARAMS

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "web_router" / "data" / "oxylabs_classic_sources.json"

LEFT_OUT = frozenset(
    {"universal", "google", "bing", "youtube_download", "google_maps", "youtube_autocomplete"}
    | {"chatgpt", "perplexity", "gemini"}
)
"""Page fetches by URL, which ``extract`` covers; a download delivered to the caller's own bucket; two sources that
refuse ``parse`` and answer with a page or with JavaScript rather than JSON; and the LLM sources, which the realtime
endpoint refuses (HTTP 422) as they run only as push-pull jobs. All seen live on 2026-11-17."""
RENDERED = "html"
"""The value a source that requires ``render`` is sent: its docs' only one."""
CONTEXT_PREFIX = "context:"


def _value(source: str, key: str, default: str | None) -> str | None:
    """A shipped input's default, refused when it reads as a sentence: a docs layout change fails here, not ships."""
    if default is not None and len(default.split()) > 1:
        raise ValueError(f"{source}.{key}: default {default!r} is a sentence, not a value; has the docs table changed?")
    return default


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
        if key in OXYLABS_CLASSIC_TRANSPORT_PARAMS:
            continue
        if key == "render" and parameter["required"]:
            fixed["render"] = RENDERED
            continue
        inputs[key] = {"required": parameter["required"], "default": _value(name, key, parameter["default"])}
    category, _, rest = found["docs"].removeprefix(DOCS + "/").partition("/")
    site = rest.split("/")[0] if rest else name
    parse = category == ECOMMERCE.strip("/") or "parse" in params
    entry: dict[str, Any] = {"name": name, "site": site, "category": category, "docs": found["docs"], "parse": parse}
    entry |= {"inputs": inputs, "context": sorted(context), "fixed": fixed}
    return entry


def build() -> dict[str, Any]:
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        crawled = pages(client)
    found = parameters(crawled)
    ecommerce = f"{DOCS}{ECOMMERCE}/"
    parsed = parsed_ecommerce(crawled)
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
