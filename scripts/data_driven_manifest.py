"""What the data-driven router's manifest says about each file of a release, for :mod:`scripts.data_driven_router`.

A release has one version. It is the delivered tool table's own, except for a refresh of the provider table alone:
then the tool table is the shipped one, byte for byte, and the release takes the version asked for while the
manifest keeps the table's own beside it.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from scripts.data_driven_providers import provider_table
from web_router.constants_data_driven import DATA_DRIVEN_MANIFEST_FILE
from web_router.routing.data_driven.providers import checked_points

TABLE_FILE = "tool_table.json"
PROVIDER_FILE = "provider_table.json"
PROVIDER_ENTRY = "provider_table"

MSG_SOURCE_COUNTS = "source examples must be non-negative integers summing to the table's request count"
MSG_VERSION = (
    "the requested release version must match the delivered table version; another is taken only while the tool "
    "table is unchanged from the shipped one: the shipped release's own, or a higher one with a lookup table"
)
MSG_VERSION_FORM = "a release version is three numbers, x.y.z; got {got!r}"
MSG_VERSION_HIGHER = "a lookup table alone takes a version higher than the shipped release's {shipped!r}; got {got!r}"
MSG_SHIPPED_MANIFEST = "the shipped manifest could not be read ({error}); fix or remove it before importing"
MSG_PROVIDER_GONE = (
    "the shipped manifest names the provider table {file!r}, which is missing; restore it, or pass LOOKUP to "
    "build a new one"
)
MSG_PROVIDER_CHANGED = (
    "the provider table differs from the shipped one, so the release needs a version higher than {shipped!r}"
)
MSG_PROVIDER_CHECKSUM = "the shipped provider table does not match its manifest checksum; restore it, or pass LOOKUP"
MSG_TOOL_CHANGED = "the tool table differs from the shipped one, so the release needs a version higher than {shipped!r}"
MSG_VERSION_BACK = "a release never moves back: {shipped!r} has shipped, and the import asks for {got!r}"
VERSION_FORM = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def shipped(folder: Path) -> dict[str, Any]:
    """The manifest of the release in ``folder``; empty when no release is there yet.

    Raises:
        ValueError: for a manifest that is there and cannot be read: treating it as no release would drop what it names.
    """
    path = folder / DATA_DRIVEN_MANIFEST_FILE
    if not path.exists():
        return {}
    try:
        manifest = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ValueError(MSG_SHIPPED_MANIFEST.format(error=type(exc).__name__)) from exc
    if not isinstance(manifest, dict):
        raise ValueError(MSG_SHIPPED_MANIFEST.format(error="not an object"))
    return manifest


def tool_entry(table: bytes) -> dict[str, Any]:
    """The manifest's entry for a delivered tool table: its checksum, request count and training sources."""
    data = json.loads(table)
    requests = data["tables"][0][""][-1]
    provenance = {name: data[name] for name in ("version", "trained_at", "sources")}
    counts = [entry["examples"] for entry in provenance["sources"]]
    if any(type(count) is not int or count < 0 for count in counts) or sum(counts) != requests:
        raise ValueError(MSG_SOURCE_COUNTS)
    return {"file": TABLE_FILE, "sha256": hashlib.sha256(table).hexdigest(), "requests": requests, **provenance}


def _numbers(version: object) -> tuple[int, int, int] | None:
    """``version`` as its three numbers, ``x.y.z``; ``None`` for anything else."""
    found = VERSION_FORM.fullmatch(version) if isinstance(version, str) else None
    return None if found is None else (int(found[1]), int(found[2]), int(found[3]))


def check_version(entry: dict[str, Any], version: str, before: dict[str, Any], lookup: bool) -> None:
    """Refuse a release version this import may not take.

    It may take the delivered table's own. While the tool table is the shipped one, byte for byte, it may also
    keep the shipped release's version (no lookup table: nothing moves back), or with a lookup table take a
    higher one than the shipped release's (the provider table alone is refreshed). A lookup table never takes
    a release back: not even to the table's own version, once a higher one shipped.
    """
    asked, released = _numbers(version), _numbers(before.get("version"))
    if asked is None:
        raise ValueError(MSG_VERSION_FORM.format(got=version))
    if released is not None and asked < released:
        raise ValueError(MSG_VERSION_BACK.format(got=version, shipped=before.get("version")))
    unchanged = (before.get("tool_table") or {}).get("sha256") == entry["sha256"]
    if before.get("version") == version and not unchanged:
        raise ValueError(MSG_TOOL_CHANGED.format(shipped=version))
    own = entry["version"] == version
    higher = released is not None and asked > released
    if lookup and unchanged and not higher and not own:
        raise ValueError(MSG_VERSION_HIGHER.format(got=version, shipped=before.get("version")))
    if not own and (not unchanged or (not lookup and before.get("version") != version)):
        raise ValueError(MSG_VERSION)


def provider_entry(lookup: Path, destination: Path) -> dict[str, Any]:
    """Build the provider table from ``lookup`` into ``destination`` and return the manifest's entry for it.

    The rows are checked as the router will check them, so a table it would refuse is never written.
    """
    providers = provider_table(json.loads(lookup.read_text(encoding="utf-8")))
    checked_points(providers["search"])
    content = (json.dumps(providers, indent=2) + "\n").encode("utf-8")
    (destination / PROVIDER_FILE).write_bytes(content)
    return {"file": PROVIDER_FILE, "sha256": hashlib.sha256(content).hexdigest(), "measured": providers["measured"]}


def carried(before: dict[str, Any], folder: Path) -> dict[str, Any] | None:
    """The shipped provider table's entry, kept when an import brings no lookup table; ``None`` when none shipped.

    Raises:
        ValueError: when a named provider table is missing or differs from its recorded checksum.
    """
    entry = before.get(PROVIDER_ENTRY)
    if entry is None:
        return None
    name = entry.get("file") if isinstance(entry, dict) else None
    if not isinstance(name, str) or not (folder / name).is_file():
        raise ValueError(MSG_PROVIDER_GONE.format(file=name))
    if hashlib.sha256((folder / name).read_bytes()).hexdigest() != entry.get("sha256"):
        raise ValueError(MSG_PROVIDER_CHECKSUM)
    return dict(entry)


def check_provider_version(before: dict[str, Any], providers: dict[str, Any], version: str) -> None:
    """Refuse a new provider table under the shipped release's version: one version names one set of tables."""
    was = before.get(PROVIDER_ENTRY)
    changed = not isinstance(was, dict) or was.get("sha256") != providers["sha256"]
    if changed and before.get("version") == version:
        raise ValueError(MSG_PROVIDER_CHANGED.format(shipped=version))


def left_out(folder: Path) -> list[dict[str, str]]:
    """The lookup rows the published provider table left out, each with why."""
    return list(json.loads((folder / PROVIDER_FILE).read_bytes())["left_out"])
