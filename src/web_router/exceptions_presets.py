"""The errors a preset store raises.

Listed in the tree of :mod:`web_router.exceptions`, which does not import them::

    WebRouterError
    └── PresetError                 a preset could not be found, kept or read
        ├── PresetNotFoundError         no preset by that name
        ├── PresetNameTakenError        create() named a preset that already exists
        ├── PresetOfAnotherToolError    a method was given a preset of another method
        ├── PresetUnreadableError       a stored preset cannot be read as one
        └── PresetStoreOutdatedError    the database schema is behind this version's
"""

from __future__ import annotations

from pathlib import Path

from web_router.constants_presets import (
    MSG_PRESET_NAME_TAKEN,
    MSG_PRESET_NOT_FOUND,
    MSG_PRESET_OF_ANOTHER_TOOL,
    MSG_PRESET_STORE_OUTDATED,
    MSG_PRESET_UNREADABLE,
    PRESET_STORE_EMPTY_REVISION,
    PRESETS_UPGRADE_COMMAND,
)
from web_router.exceptions import WebRouterError


class PresetError(WebRouterError):
    """Base class for every preset error."""


class PresetNotFoundError(PresetError):
    """The store has no preset by that name."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(MSG_PRESET_NOT_FOUND.format(name=name))


class PresetNameTakenError(PresetError):
    """Another preset in the store has that name: names name presets, so they are unique, and never overwritten."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(MSG_PRESET_NAME_TAKEN.format(name=name))


class PresetOfAnotherToolError(PresetError):
    """A method was given a preset of another method: each preset runs the one method it was made for."""

    def __init__(self, name: str, tool: str) -> None:
        self.name = name
        self.tool = tool
        super().__init__(MSG_PRESET_OF_ANOTHER_TOOL.format(name=name, tool=tool))


class PresetUnreadableError(PresetError):
    """A stored preset cannot be read as one: a file that does not parse, or holds params this version refuses."""

    def __init__(self, where: Path | str, detail: str) -> None:
        self.where = str(where)
        super().__init__(MSG_PRESET_UNREADABLE.format(where=self.where, detail=detail))


class PresetStoreOutdatedError(PresetError):
    """The presets database is behind the schema this version ships: run its migrations first.

    The store never upgrades a database on its own, since a schema change is the
    database owner's call; :func:`web_router.presets_sql.upgrade` does it.
    """

    def __init__(self, current: str | None, head: str) -> None:
        self.current = current
        self.head = head
        super().__init__(
            MSG_PRESET_STORE_OUTDATED.format(
                current=current or PRESET_STORE_EMPTY_REVISION, head=head, command=PRESETS_UPGRADE_COMMAND
            )
        )
