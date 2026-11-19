"""The frozen folder's files: where they live, the row fields, and reading and checking them against ``SHA256SUMS``.

Kept apart from ``frozen.py``, which re-exports every name here. Imported as a
sibling, by name, as ``_common`` is.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from _frozen_checks import check_failed_rows

HERE = Path(__file__).resolve().parent
FROZEN_DIR = HERE / "frozen"
JOINED_DIR = HERE / "results" / "frozen-joined"
SUMS_FILE = "SHA256SUMS"
MANIFEST_FILE = "manifest.json"
SCHEMA_VERSION = 1
SUFFIX = ".jsonl.gz"

FIELDS = ("set", "question_id", "provider", "method", "mode", "grade", "latency_s", "cost_usd", "error")
"""A frozen row's fields, in order. Nothing else: no question, answer or gold text, no raw message."""

MSG_SUMS_MISMATCH = "{name}: SHA-256 {actual} does not match SHA256SUMS ({expected}); the file changed or is damaged"
MSG_SUMS_MISSING = "{name} is in {folder} but not in SHA256SUMS"
MSG_FILE_MISSING = "{name} is in SHA256SUMS but not in {folder}"


def gzip_jsonl(rows: Iterable[Mapping[str, Any]]) -> bytes:
    """Rows as gzipped JSON lines, byte for byte the same on every run: no file name, no timestamp."""
    text = "".join(json.dumps({key: row[key] for key in FIELDS}, ensure_ascii=False) + "\n" for row in rows)
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0, compresslevel=9) as handle:
        handle.write(text.encode("utf-8"))
    return buffer.getvalue()


def read_rows(path: Path) -> list[dict[str, Any]]:
    """A frozen file's rows."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_sums(folder: Path) -> dict[str, str]:
    """``SHA256SUMS`` as file name -> digest, in ``sha256sum`` format."""
    sums: dict[str, str] = {}
    for line in (folder / SUMS_FILE).read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, name = line.split(maxsplit=1)
            sums[name.lstrip("*")] = digest
    return sums


def verify(folder: Path = FROZEN_DIR) -> dict[str, str]:
    """Every frozen file checked against ``SHA256SUMS``, both ways, and the manifest's failed rows against the rows.

    The digests, or ``SystemExit`` on a fault.
    """
    sums = read_sums(folder)
    present = {path.name for path in folder.iterdir() if path.name != SUMS_FILE and path.is_file()}
    for name in sorted(present - set(sums)):
        raise SystemExit(MSG_SUMS_MISSING.format(name=name, folder=folder))
    for name, expected in sorted(sums.items()):
        if name not in present:
            raise SystemExit(MSG_FILE_MISSING.format(name=name, folder=folder))
        actual = hashlib.sha256((folder / name).read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(MSG_SUMS_MISMATCH.format(name=name, actual=actual, expected=expected))
    manifest = json.loads((folder / MANIFEST_FILE).read_text(encoding="utf-8"))
    for set_name, entry in manifest["sets"].items():
        check_failed_rows(set_name, entry, read_rows(folder / (set_name + SUFFIX)))
    return sums
