"""Presets: saved settings a call names instead of repeating them.

A preset is a versioned definition -- ``acme.supplier-research@1.0.0`` -- holding
settings for one or more methods under each method's own argument names. A
router takes one of its own (``WebRouter(preset=...)``) and each call can name
one more (``preset=``). For every setting the call uses, in this order:

1. the argument the call passed,
2. the per-call preset,
3. the router's preset,
4. the method's own default.

A section may lock a setting by leaving it out of ``overridable``; replacing a
locked setting with a different value raises :class:`~web_router.PresetValidationError`
rather than letting either side win. Everything here runs before the backend is
called -- an unknown reference, a preset for another method, a setting that is
not the method's, a value of the wrong type -- so a preset problem never bills.

Definitions come only from what was loaded or passed in. Nothing is fetched,
nothing is upgraded on its own, and a version once loaded never changes: a
different definition under the same reference is refused.
"""

from __future__ import annotations

import builtins
import threading
from collections.abc import Mapping
from os import PathLike
from pathlib import Path
from typing import Any

from web_router.constants import DEFAULT_PROVIDER
from web_router.constants_presets import (
    MSG_PRESET_ALREADY_LOADED,
    MSG_PRESET_DEFINED_TWICE,
    MSG_PRESET_EMPTY_DIRECTORY,
    PRESET_FILE_SUFFIXES,
)
from web_router.exceptions_presets import PresetCompatibilityError, PresetError, PresetNotFoundError
from web_router.presets_check import preflight
from web_router.presets_layers import PresetResolution, applied
from web_router.presets_load import _changed, _checked, _copy, _identical, _read, _version_key, parse_ref
from web_router.types_meta import (
    PresetComponent,
    PresetSource,
)
from web_router.types_presets import Preset, PresetCheck

__all__ = ["PresetRegistry", "PresetResolution", "parse_ref"]


class PresetRegistry:
    """The presets one router can use, and the router's own default.

    Every router has one, as ``router.presets``. Load definitions into it with
    :meth:`load`, or pass a :class:`~web_router.Preset` straight to ``preset=``,
    which adds it. Lookups need an exact ``id@version``.
    """

    def __init__(
        self,
        *,
        client: str | Preset | None = None,
        default_provider: str = DEFAULT_PROVIDER,
        provider_config: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        """
        Args:
            client: The router's own preset, applied to every call it applies
                to (not ``plan()`` or ``run()``). A reference is checked for form now and looked up on each
                call, so it can be loaded after the router is built.
            default_provider: The backend a call with no ``provider`` goes to,
                which :meth:`validate` checks settings against.
            provider_config: The router's ``provider_config``, which decides
                the backend an unnamed ``answer()`` goes to, so :meth:`validate`
                picks the one the call would.
        """
        self._presets: dict[str, Preset] = {}
        self._lock = threading.Lock()
        self.default_provider = default_provider
        self._provider_config: Mapping[str, Mapping[str, Any]] = provider_config or {}
        self.client: str | None = None if client is None else self._reference(client)

    def load(self, path: str | PathLike[str]) -> builtins.list[Preset]:
        """Load every preset in a JSON or YAML file, or in every such file directly inside a directory.

        A file holds one definition or a list of them. All of them are checked
        before any is added, so a bad file leaves the registry as it was.
        Reloading a definition that is already loaded is a no-op. YAML needs
        PyYAML; JSON needs nothing.

        Raises:
            PresetValidationError: for a definition that is malformed, holds a
                setting its method does not take or a value of the wrong type,
                or differs from another under the same reference, loaded
                before or in this same call.
            PresetError: for a file that cannot be read or parsed, or a
                directory with no preset files in it.
        """
        source = Path(path)
        if source.is_dir():
            files = sorted(item for item in source.iterdir() if item.is_file() and item.suffix in PRESET_FILE_SUFFIXES)
            if not files:
                raise PresetError(MSG_PRESET_EMPTY_DIRECTORY.format(path=source))
        else:
            files = [source]
        batch: dict[str, Preset] = {}
        for preset in (_checked(data, where=str(file)) for file in files for data in _read(file)):
            if not _identical(batch.setdefault(preset.ref, preset), preset):
                raise _changed(preset.ref, MSG_PRESET_DEFINED_TWICE)
        with self._lock:
            for preset in batch.values():
                self._refuse_change(preset)
            self._presets.update(batch)
        return [_copy(preset) for preset in batch.values()]

    def add(self, preset: Preset | Mapping[str, Any]) -> Preset:
        """Add one definition, a :class:`~web_router.Preset` or the mapping a file would hold, and return it.

        The registry keeps its own copy, so changing what was passed in, or
        what comes back, changes nothing a call uses.

        Raises:
            PresetValidationError: as for :meth:`load`, or for a different
                definition under a reference that is already loaded.
        """
        checked = self._stored(_checked(preset))
        return _copy(checked)

    def get(self, ref: str) -> Preset:
        """A copy of the preset loaded under the exact reference ``ref``.

        Raises:
            PresetValidationError: for a reference that is not exact.
            PresetNotFoundError: for one nothing is loaded under, naming the
                versions of the same id that are.
        """
        return _copy(self._get(ref))

    def _get(self, ref: str) -> Preset:
        preset_id, _ = parse_ref(ref)
        with self._lock:
            preset = self._presets.get(ref)
            if preset is None:
                versions = [known.version for known in self._presets.values() if known.id == preset_id]
                raise PresetNotFoundError(ref, versions)
            return preset

    def _stored(self, preset: Preset) -> Preset:
        with self._lock:
            self._refuse_change(preset)
            return self._presets.setdefault(preset.ref, preset)

    def resolve(self, component: PresetComponent, preset: str | Preset | None, **explicit: Any) -> PresetResolution:
        """Apply the per-call ``preset`` and the router's own to one call's arguments.

        ``explicit`` is every setting the method takes, ``None`` where the call
        left it unset. A per-call preset that does not apply to ``component``
        raises; the router's own simply does not apply.

        Raises:
            PresetNotFoundError: for a preset that is not loaded.
            PresetCompatibilityError: for a per-call preset for another method.
            PresetValidationError: for a locked setting replaced with another value.
        """
        layers: builtins.list[tuple[PresetSource, Preset]] = []
        if preset is not None:
            call = self._stored(_checked(preset)) if isinstance(preset, Preset) else self._get(preset)
            if component not in call.applies_to:
                raise PresetCompatibilityError(call.ref, component, call.applies_to)
            layers.append(("request", call))
        if self.client is not None:
            client = self._get(self.client)
            if component in client.applies_to:
                layers.append(("client", client))
        if not layers:
            return PresetResolution(component, dict(explicit))

        return applied(component, layers, explicit)

    def validate(
        self,
        component: PresetComponent,
        request: Mapping[str, Any] | None = None,
        *,
        preset: str | Preset | None = None,
    ) -> PresetCheck:
        """Check a call against its presets and its backend without making it.

        ``request`` is the call's arguments as the method takes them --
        ``{"query": ..., "max_results": 3}`` for a search. The report gives
        the presets that applied and the settings they supplied, and lists as
        issues every preset problem, any argument the method does not take,
        each setting the backend does not map and a format it cannot return.
        It also runs the checks the backend's class makes before a call bills:
        an option it needs left unset, a value it maps but cannot serve, an
        option missing its partner and an option it refuses outright. Only the
        class is read: nothing is sent and no key is needed.
        """
        return preflight(self, component, request, preset)

    def _reference(self, preset: str | Preset) -> str:
        if isinstance(preset, Preset):
            return self.add(preset).ref
        parse_ref(preset)
        return preset

    def _refuse_change(self, preset: Preset) -> None:
        known = self._presets.get(preset.ref)
        if known is not None and not _identical(known, preset):
            raise _changed(preset.ref, MSG_PRESET_ALREADY_LOADED)

    def list(self, component: PresetComponent | None = None) -> builtins.list[Preset]:
        """A copy of every loaded preset, by reference, or only those that apply to ``component``."""
        with self._lock:
            presets = sorted(self._presets.values(), key=lambda preset: (preset.id, _version_key(preset.version)))
        return [_copy(preset) for preset in presets if component is None or component in preset.applies_to]

    def __len__(self) -> int:
        return len(self._presets)

    def __repr__(self) -> str:
        return f"<PresetRegistry presets={len(self)} client={self.client!r}>"
