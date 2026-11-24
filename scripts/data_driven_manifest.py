"""What the data-driven router's manifest says about each file of a release, for :mod:`scripts.data_driven_router`.

A release has one version. It is the delivered tool table's own, except for a refresh of the provider table alone:
then the tool table is the shipped one, byte for byte, and the release takes the version asked for while the
manifest keeps the table's own beside it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.data_driven_providers import provider_table
from web_router.constants_data_driven import DATA_DRIVEN_MANIFEST_FILE

TABLE_FILE = "tool_table.json"
PROVIDER_FILE = "provider_table.json"
PROVIDER_ENTRY = "provider_table"

MSG_SOURCE_COUNTS = "source examples must be non-negative integers summing to the table's request count"
MSG_VERSION = (
    "the requested release version must match the delivered table version; another version is taken only with a "
    "lookup table and a tool table unchanged from the shipped one"
)


def shipped(folder: Path) -> dict[str, Any]:
    """The manifest of the release in ``folder``; empty when there is none to read."""
    try:
        manifest = json.loads((folder / DATA_DRIVEN_MANIFEST_FILE).read_bytes())
    except (OSError, ValueError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


def tool_entry(table: bytes) -> dict[str, Any]:
    """The manifest's entry for a delivered tool table: its checksum, request count and training sources."""
    data = json.loads(table)
    requests = data["tables"][0][""][-1]
    provenance = {name: data[name] for name in ("version", "trained_at", "sources")}
    counts = [entry["examples"] for entry in provenance["sources"]]
    if any(type(count) is not int or count < 0 for count in counts) or sum(counts) != requests:
        raise ValueError(MSG_SOURCE_COUNTS)
    return {"file": TABLE_FILE, "sha256": hashlib.sha256(table).hexdigest(), "requests": requests, **provenance}


def check_version(entry: dict[str, Any], version: str, before: dict[str, Any], lookup: bool) -> None:
    """Refuse a release version other than the delivered table's, unless only the provider table is refreshed."""
    if entry["version"] == version:
        return
    unchanged = (before.get("tool_table") or {}).get("sha256") == entry["sha256"]
    if not (lookup and unchanged):
        raise ValueError(MSG_VERSION)


def provider_entry(lookup: Path, destination: Path) -> dict[str, Any]:
    """Build the provider table from ``lookup`` into ``destination`` and return the manifest's entry for it."""
    providers = provider_table(json.loads(lookup.read_text(encoding="utf-8")))
    content = (json.dumps(providers, indent=2) + "\n").encode("utf-8")
    (destination / PROVIDER_FILE).write_bytes(content)
    return {"file": PROVIDER_FILE, "sha256": hashlib.sha256(content).hexdigest(), "measured": providers["measured"]}


def carried(before: dict[str, Any], folder: Path) -> dict[str, Any] | None:
    """The shipped provider table's entry, kept when an import brings no lookup table and its file is still there."""
    entry = before.get(PROVIDER_ENTRY)
    return entry if isinstance(entry, dict) and (folder / str(entry.get("file"))).is_file() else None


def left_out(folder: Path) -> list[dict[str, str]]:
    """The lookup rows the published provider table left out, each with why."""
    try:
        rows = json.loads((folder / PROVIDER_FILE).read_bytes())["left_out"]
    except (OSError, ValueError, KeyError):
        return []
    return list(rows)
