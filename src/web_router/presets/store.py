"""Where presets live: the store interface a router reads them through, and the in-memory store it defaults to."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from typing import Protocol, TypeVar, runtime_checkable

from web_router.constants_presets import LOG_PRESET_SKIPPED
from web_router.exceptions_presets import PresetNameTakenError, PresetUnreadableError
from web_router.presets.model import Preset

_T = TypeVar("_T")

logger = logging.getLogger(__name__)


def readable(read: Callable[[_T], Preset], stored: Iterable[_T]) -> list[Preset]:
    """Every stored preset ``read`` can read, each one it cannot logged and left out: one bad entry must not hide
    the rest of a listing, while a lookup by name still raises."""
    presets: list[Preset] = []
    for entry in stored:
        try:
            presets.append(read(entry))
        except PresetUnreadableError as exc:
            logger.warning(LOG_PRESET_SKIPPED, exc)
    return presets


@runtime_checkable
class PresetStore(Protocol):
    """Anything that can find, list and keep presets by name.

    Synchronous on purpose: a store reads or writes one small record, and the
    async router calls it directly rather than through a thread. A store whose
    backend is slow enough to matter should cache. There is no account or
    tenant in the interface: a service holding many tenants' presets wraps one
    store per tenant, or one that scopes every call to the tenant it was built
    for. Built in: :class:`InMemoryPresetStore`, :class:`FilePresetStore` and
    :class:`~web_router.presets_sql.SqlPresetStore`.
    """

    def get(self, name: str) -> Preset | None:
        """The preset named ``name``, or ``None`` when there is none."""
        ...

    def list(self) -> list[Preset]:
        """Every preset in the store, in no particular order."""
        ...

    def create(self, preset: Preset) -> None:
        """Keep ``preset``.

        Raises:
            PresetNameTakenError: the store already has a preset by that name;
                the one stored is left as it was.
        """
        ...


class InMemoryPresetStore:
    """Presets held in this process, gone when it exits. Safe to share between threads.

    The store a router uses when it is given none.
    """

    def __init__(self, presets: Iterable[Preset] = ()) -> None:
        """
        Args:
            presets: Presets to start with, created in order, so two by one name raise
                :class:`~web_router.PresetNameTakenError`.
        """
        self._presets: dict[str, Preset] = {}
        self._lock = threading.Lock()
        for preset in presets:
            self.create(preset)

    def get(self, name: str) -> Preset | None:
        with self._lock:
            preset = self._presets.get(name)
        return None if preset is None else preset.model_copy(deep=True)

    def list(self) -> list[Preset]:
        with self._lock:
            presets = list(self._presets.values())
        return [preset.model_copy(deep=True) for preset in presets]

    def create(self, preset: Preset) -> None:
        with self._lock:
            if preset.name in self._presets:
                raise PresetNameTakenError(preset.name)
            self._presets[preset.name] = preset.model_copy(deep=True)
