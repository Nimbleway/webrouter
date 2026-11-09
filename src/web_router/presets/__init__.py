"""Presets: one router method and the arguments saved for it, named so any run can call it.

:class:`Preset` is the record, :class:`PresetStore` the interface a router
reads presets through, and :class:`InMemoryPresetStore` and
:class:`FilePresetStore` two stores that need nothing installed;
:class:`~web_router.presets_sql.SqlPresetStore`, in :mod:`web_router.presets_sql`,
keeps them in SQLite or Postgres with the ``sql`` extra.
Every router method takes ``preset=<name>``: the preset's params, with the
call's own arguments on top.
"""

from __future__ import annotations

from web_router.presets.file import FilePresetStore
from web_router.presets.model import Preset
from web_router.presets.params import PRESET_TOOLS, PresetTool, tool_params
from web_router.presets.store import InMemoryPresetStore, PresetStore

__all__ = [
    "PRESET_TOOLS",
    "FilePresetStore",
    "InMemoryPresetStore",
    "Preset",
    "PresetStore",
    "PresetTool",
    "tool_params",
]
