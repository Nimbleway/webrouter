"""Presets in a SQL database: SQLite or Postgres, one URL, with the schema migrations shipped in the package.

Install the ``sql`` extra. Before the first use, and after each upgrade of
web-router that ships a migration, bring the database's schema up to date::

    python -m web_router.presets_sql upgrade sqlite:///presets.db

or from Python, :func:`upgrade`. A :class:`SqlPresetStore` on a database that
is behind refuses to start rather than reading a table it does not know; it
never changes the schema itself, which is the database owner's call. The table
is ``web_router_presets`` and Alembic's version record is
``web_router_presets_alembic_version``, so a database that already runs Alembic
for its own app can hold both. Like Optuna's ``RDBStorage``: one URL, any
SQLAlchemy dialect, a driver installed for it -- ``psycopg`` for
``postgresql+psycopg://``.

Nothing here imports SQLAlchemy until a store is built or a migration run, so
``import web_router`` works without the extra.
"""

from __future__ import annotations

from types import ModuleType
from typing import TYPE_CHECKING

from pydantic import ValidationError

from web_router.constants_presets import EXTRA_SQL, PRESET_SQL_STORE, PRESETS_SQL_WHERE, PRESETS_TABLE
from web_router.exceptions import ProviderNotInstalledError
from web_router.exceptions_presets import PresetStoreOutdatedError, PresetUnreadableError
from web_router.presets.model import Preset
from web_router.presets.params import problems
from web_router.presets.store import readable

if TYPE_CHECKING:
    from sqlalchemy import Engine

__all__ = ["SqlPresetStore", "upgrade"]


def _backend() -> ModuleType:
    try:
        from web_router.presets_sql import _sql
    except ImportError as exc:
        raise ProviderNotInstalledError(PRESET_SQL_STORE, EXTRA_SQL) from exc
    return _sql


def upgrade(url: str) -> str:
    """Run every shipped migration the database at ``url`` has not run, and return the revision it is now at.

    Safe to run again: a database already at the head is left as it is.
    """
    revision: str = _backend().upgrade(url)
    return revision


class SqlPresetStore:
    """Presets kept in the database at ``url``. Safe to share between threads.

    One ``web_router_presets`` row per preset -- name, tool, description, when
    it was made -- pointing at one row of its tool's params table, a typed
    column per param, NULL where the preset leaves it unset. A stored preset
    this version cannot read is refused by :meth:`get` with
    :class:`~web_router.PresetUnreadableError` and left out of :meth:`list`,
    with a warning logged, so one bad row never hides the rest.

    Raises:
        PresetStoreOutdatedError: on construction, when the database has not
            run every migration this version ships -- run :func:`upgrade`.
        ProviderNotInstalledError: without the ``sql`` extra.
    """

    def __init__(self, url: str) -> None:
        backend = _backend()
        self._backend = backend
        self._engine: Engine = backend.create_engine(url)
        current, head = backend.current_revision(self._engine), backend.head_revision()
        if current != head:
            self._engine.dispose()
            raise PresetStoreOutdatedError(
                current, head, newer=current is not None and not backend.known_revision(current)
            )
        self._tables = backend.tables(self._engine)

    def get(self, name: str) -> Preset | None:
        rows = self._backend.read(self._engine, self._tables, name)
        return _preset(rows[0]) if rows else None

    def list(self) -> list[Preset]:
        return readable(_preset, self._backend.read(self._engine, self._tables))

    def create(self, preset: Preset) -> None:
        self._backend.write(self._engine, self._tables, preset.model_dump(mode="json"))

    def close(self) -> None:
        """Close the store's connections. The router never does: the store is the caller's."""
        self._engine.dispose()


def _preset(row: dict[str, object]) -> Preset:
    try:
        return Preset.model_validate(row)
    except ValidationError as exc:
        raise PresetUnreadableError(
            PRESETS_SQL_WHERE.format(table=PRESETS_TABLE, name=row["name"]), problems(exc)
        ) from exc
