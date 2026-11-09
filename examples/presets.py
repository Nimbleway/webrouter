"""Presets: a router method's optional arguments, saved under a name and used with `preset=`.

A preset is one method -- search, extract, answer, research, browse,
run_template or open_browser -- and the optional arguments saved for it,
checked as the method checks them. `router.search(query, preset="eu_semiconductor_news")`
calls search with the preset's arguments and the call's own on top; the
response says which preset ran, and with what, in `response.preset`.

Three stores keep them: in memory (the default), as JSON or YAML files in a
directory, and in SQLite or Postgres with the `sql` extra. The SQL part runs
only when SQLAlchemy and Alembic are installed.

Needs no key: the extract goes to `auto`, which falls back to the keyless tier.

Run it with::

    make setup   # in your webrouter clone: every extra
    uv run python examples/presets.py
"""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

from pydantic import ValidationError

from web_router import (
    FilePresetStore,
    InMemoryPresetStore,
    Preset,
    PresetNameTakenError,
    PresetOfAnotherToolError,
    PresetStore,
    WebRouter,
)

# The managed web-gateway presets' own examples, as the library's methods take them.
EU_SEMICONDUCTOR_NEWS = Preset(
    name="eu_semiconductor_news",
    tool="search",
    description="EU chip-industry news, English, trade press only",
    params={
        "max_results": 20,
        "search_depth": "fast",
        "country": "DE",
        "locale": "en",
        "include_domains": ["reuters.com", "eetimes.eu", "semiengineering.com"],
        "exclude_domains": ["reddit.com"],
    },
)
PAGE_TEXT = Preset(name="page_text", tool="extract", description="Pages as plain text", params={"format": "text"})
SUPPLIER_PROFILE = Preset(
    name="supplier_profile",
    tool="research",
    description="Structured supplier profiles from official and filings sources",
    params={
        "effort": "high",
        "include_domains": ["sec.gov", "companieshouse.gov.uk", "opencorporates.com"],
        "exclude_domains": ["wikipedia.org"],
        "output_schema": {
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "headquarters_country": {"type": "string"},
                "employees": {"type": "integer"},
                "main_products": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["company"],
        },
    },
)
PAGE_HEADING = Preset(
    name="page_heading",
    tool="browse",
    description="Read one page heading, few steps",
    params={
        "max_steps": 3,
        "allowed_domains": ["example.com", "iana.org"],
        "output_schema": {"type": "object", "properties": {"heading": {"type": "string"}}},
    },
)
EXAMPLES = (EU_SEMICONDUCTOR_NEWS, PAGE_TEXT, SUPPLIER_PROFILE, PAGE_HEADING)


def run(store: PresetStore, label: str) -> None:
    """Keep the examples in ``store``, then read a page with `page_text`, the one that needs no key.

    The others run the same way -- ``router.search("ASML export licences", preset="eu_semiconductor_news")``,
    ``router.research("Fill in each supplier's profile", preset="supplier_profile", records=[...])`` -- with a
    key for a backend that maps their options.
    """
    for preset in EXAMPLES:
        store.create(preset)
    with WebRouter(presets=store) as router:
        response = router.extract("https://example.com", preset="page_text")
        print(f"[{label}] {sorted(preset.name for preset in store.list())}")
        print(f"    {response.preset}: {response.documents[0].content[:60]!r}")


def memory() -> None:
    run(InMemoryPresetStore(), "memory")


def files(directory: Path) -> None:
    run(FilePresetStore(directory), "files")
    print(f"    wrote {sorted(path.name for path in directory.iterdir())}")
    # A teammate's preset, written by hand in YAML (with the `yaml` extra) or JSON, is read at the next call.
    (directory / "pages.json").write_text('{"name": "page_html", "tool": "extract", "params": {"format": "html"}}')
    print(f"    the store now holds {sorted(preset.name for preset in FilePresetStore(directory).list())}")


def sqlite(database: Path) -> None:
    from web_router.presets_sql import SqlPresetStore, upgrade

    url = f"sqlite:///{database}"
    # The schema is the database owner's: run the shipped migrations once, and again after an upgrade.
    # From a shell: python -m web_router.presets_sql upgrade sqlite:///presets.db
    print(f"[sqlite] schema at revision {upgrade(url)}")
    store = SqlPresetStore(url)
    try:
        run(store, "sqlite")
    finally:
        store.close()


def refusals() -> None:
    """What a preset will not hold, what a store will not overwrite, and a preset used for another method."""
    try:
        Preset(name="typo", tool="search", params={"max_result": 20})
    except ValidationError as exc:
        print(f"[refused] {exc.errors()[0]['msg']}")
    store = InMemoryPresetStore([EU_SEMICONDUCTOR_NEWS])
    try:
        store.create(EU_SEMICONDUCTOR_NEWS)
    except PresetNameTakenError as exc:
        print(f"[refused] {exc}")
    try:
        WebRouter(presets=store).extract("https://example.com", preset="eu_semiconductor_news")
    except PresetOfAnotherToolError as exc:
        print(f"[refused] {exc}")


if __name__ == "__main__":
    memory()
    with tempfile.TemporaryDirectory() as scratch:
        files(Path(scratch) / "presets")
        if importlib.util.find_spec("sqlalchemy") and importlib.util.find_spec("alembic"):
            sqlite(Path(scratch) / "presets.db")
        else:
            print("[sqlite] skipped: install the sql extra (uv sync --extra sql)")
    refusals()
