"""Presets kept as files in one directory, for a team that reviews them in git."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from web_router.constants_presets import (
    EXTRA_YAML,
    MSG_PRESET_DEFINED_TWICE,
    PRESET_FILE_ENCODING,
    PRESET_FILE_INDENT,
    PRESET_FILE_JSON,
    PRESET_FILE_YAML,
    PRESET_YAML_READER,
)
from web_router.exceptions import ProviderNotInstalledError
from web_router.exceptions_presets import PresetNameTakenError, PresetUnreadableError
from web_router.presets.model import Preset
from web_router.presets.params import problems
from web_router.presets.store import readable


class FilePresetStore:
    """Presets read from the ``*.json`` files in ``directory``, and ``*.yaml`` / ``*.yml`` with the ``yaml`` extra.

    A file holds one preset, an object, or several, a list of them; other files
    in the directory are not read. Every read goes back to the files, so a
    preset edited or added on disk is seen at the next call. A preset this
    version cannot read -- in a file that does not parse, refused by its
    rules, or one name in two places -- is left out of :meth:`list` with a
    warning logged, and :meth:`get` of its name raises
    :class:`~web_router.PresetUnreadableError` naming the file; a file that
    does not parse goes by its stem. A YAML file found without PyYAML installed
    raises :class:`~web_router.ProviderNotInstalledError`.

    :meth:`create` writes ``<name>.json`` and never overwrites a file, so on a
    case-insensitive filesystem (macOS and Windows by default) it refuses
    ``report`` beside ``Report`` with :class:`~web_router.PresetNameTakenError`;
    one file listing both still holds both. A missing directory reads as
    empty, and is made by the first create.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self._lock = threading.Lock()

    def get(self, name: str) -> Preset | None:
        entry = self._read().get(name)
        return None if entry is None else _usable(entry)

    def list(self) -> list[Preset]:
        return readable(_usable, self._read().values())

    def create(self, preset: Preset) -> None:
        document = preset.model_dump(mode="json")
        if document["description"] is None:
            del document["description"]
        with self._lock:
            if preset.name in self._read():
                raise PresetNameTakenError(preset.name)
            self.directory.mkdir(parents=True, exist_ok=True)
            try:
                with (self.directory / f"{preset.name}{PRESET_FILE_JSON}").open(
                    "x", encoding=PRESET_FILE_ENCODING
                ) as file:
                    json.dump(document, file, indent=PRESET_FILE_INDENT)
                    file.write("\n")
            except FileExistsError as exc:
                raise PresetNameTakenError(preset.name) from exc

    def _read(self) -> dict[str, Preset | PresetUnreadableError]:
        """Every preset by name, or the reason it cannot be read; a file that does not parse is under its stem."""
        if not self.directory.is_dir():
            return {}
        found: dict[str, Preset | PresetUnreadableError] = {}
        where: dict[str, Path] = {}
        for path in sorted(self.directory.iterdir()):
            if not path.is_file() or (path.suffix != PRESET_FILE_JSON and path.suffix not in PRESET_FILE_YAML):
                continue
            for name, entry in _presets_in(path):
                if name in found:
                    entry = PresetUnreadableError(path, MSG_PRESET_DEFINED_TWICE.format(name=name, other=where[name]))
                found[name] = entry
                where.setdefault(name, path)
        return found


def _presets_in(path: Path) -> list[tuple[str, Preset | PresetUnreadableError]]:
    yaml = None if path.suffix == PRESET_FILE_JSON else _yaml()
    # A file that cannot be read -- not UTF-8 (UnicodeDecodeError is a ValueError), no permission -- is one bad
    # entry like a file that does not parse, never a failure of the whole store.
    unparsable: tuple[type[Exception], ...] = (ValueError, OSError)
    if yaml is not None:
        unparsable = (*unparsable, yaml.YAMLError)
    try:
        text = path.read_text(encoding=PRESET_FILE_ENCODING)
        document = json.loads(text) if yaml is None else yaml.safe_load(text)
    except unparsable as exc:
        return [(path.stem, PresetUnreadableError(path, problems(exc)))]
    entries: list[tuple[str, Preset | PresetUnreadableError]] = []
    for each in document if isinstance(document, list) else [document]:
        name = each.get("name") if isinstance(each, dict) else None
        try:
            preset = Preset.model_validate(each)
        except ValidationError as exc:
            entries.append((str(name) if name is not None else path.stem, PresetUnreadableError(path, problems(exc))))
        else:
            entries.append((preset.name, preset))
    return entries


def _usable(entry: Preset | PresetUnreadableError) -> Preset:
    if isinstance(entry, PresetUnreadableError):
        raise entry
    return entry


def _yaml() -> Any:
    try:
        import yaml  # type: ignore[import-untyped,unused-ignore]
    except ImportError as exc:
        raise ProviderNotInstalledError(PRESET_YAML_READER, EXTRA_YAML) from exc
    return yaml
