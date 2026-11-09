"""The SQLAlchemy and Alembic side of :class:`~web_router.presets_sql.SqlPresetStore`, imported only once one is built.

The tables are the shipped migrations' alone: the store reflects them from the
database when it is built, so the schema is written down in one place,
``migrations/versions/``, and an old migration keeps meaning what it meant
when it ran.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import Engine, MetaData, Table, create_engine, insert, select
from sqlalchemy.exc import IntegrityError

from web_router.constants_presets import (
    PRESETS_PARAMS_ID,
    PRESETS_PARAMS_TABLE,
    PRESETS_TABLE,
    PRESETS_VERSION_TABLE,
)
from web_router.exceptions_presets import PresetNameTakenError
from web_router.presets.params import PRESET_TOOLS

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
CONNECTION = "connection"
"""The key under which :func:`upgrade` hands ``migrations/env.py`` its connection."""


@dataclass(frozen=True)
class Tables:
    presets: Table
    params: dict[str, Table]


def tables(engine: Engine) -> Tables:
    """The presets table and each tool's params table, as the database has them."""
    metadata = MetaData()
    names = {tool: PRESETS_PARAMS_TABLE.format(tool=tool) for tool in PRESET_TOOLS}
    metadata.reflect(engine, only=[PRESETS_TABLE, *names.values()])
    return Tables(metadata.tables[PRESETS_TABLE], {tool: metadata.tables[name] for tool, name in names.items()})


def _config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    return config


def head_revision() -> str:
    """The schema revision this version of web-router ships."""
    head: str | None = ScriptDirectory.from_config(_config()).get_current_head()
    assert head is not None, "the package ships at least one migration"
    return head


def known_revision(revision: str) -> bool:
    """Whether this version ships ``revision``: one it does not was written by a newer web-router."""
    try:
        return ScriptDirectory.from_config(_config()).get_revision(revision) is not None
    except CommandError:
        return False


def current_revision(engine: Engine) -> str | None:
    """The revision ``engine``'s database is at; ``None`` when no migration has run there."""
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"version_table": PRESETS_VERSION_TABLE})
        revision: str | None = context.get_current_revision()
        return revision


def upgrade(url: str) -> str:
    """Run every shipped migration ``url``'s database has not run yet, and return the revision it is now at."""
    engine = create_engine(url)
    try:
        config = _config()
        with engine.begin() as connection:
            # Handed over, not put in the config as a URL: a password's `%` would read as configparser syntax.
            config.attributes[CONNECTION] = connection
            command.upgrade(config, "head")
        return head_revision()
    finally:
        engine.dispose()


def _params_by_id(connection: Any, table: Table, ids: Iterable[int]) -> dict[int, dict[str, Any] | None]:
    """Each params row by id, NULLs left out so they read back unset; ``None`` for one whose JSON does not decode."""
    wanted = list(ids)
    try:
        rows = list(connection.execute(select(table).where(table.c.id.in_(wanted))).mappings())
    except ValueError:
        # One row's malformed JSON -- possible on SQLite, which keeps JSON as text -- fails the whole fetch, so
        # read the rows one by one and mark only that one unreadable.
        return {row_id: _one(connection, table, row_id) for row_id in wanted}
    return {row["id"]: {key: value for key, value in row.items() if key != "id" and value is not None} for row in rows}


def _one(connection: Any, table: Table, row_id: int) -> dict[str, Any] | None:
    try:
        row = connection.execute(select(table).where(table.c.id == row_id)).mappings().first()
    except ValueError:
        return None
    return None if row is None else {key: value for key, value in row.items() if key != "id" and value is not None}


def read(engine: Engine, schema: Tables, name: str | None = None) -> list[dict[str, Any]]:
    """The stored presets, each as a preset's fields: the one named ``name``, or all of them."""
    query = select(schema.presets)
    if name is not None:
        query = query.where(schema.presets.c.name == name)
    with engine.connect() as connection:
        rows = [dict(row) for row in connection.execute(query).mappings()]
        params: dict[str, dict[int, dict[str, Any] | None]] = {}
        for tool, table in schema.params.items():
            ids = [row[PRESETS_PARAMS_ID.format(tool=tool)] for row in rows if row["tool"] == tool]
            params[tool] = _params_by_id(connection, table, ids) if ids else {}
    return [
        {
            "name": row["name"],
            "tool": row["tool"],
            "description": row["description"],
            # A params row that is missing or cannot be decoded reads as None, which no preset accepts: the preset
            # is unreadable, never silently a preset with no settings.
            "params": params.get(row["tool"], {}).get(row[PRESETS_PARAMS_ID.format(tool=row["tool"])]),
        }
        for row in rows
    ]


def write(engine: Engine, schema: Tables, preset: dict[str, Any]) -> None:
    """Insert the preset and its params row in one transaction, refusing a name already stored: the primary key
    decides, so two writers cannot both win, and the loser's params row goes with its rollback."""
    tool = preset["tool"]
    now = datetime.now(timezone.utc)
    try:
        with engine.begin() as connection:
            inserted = connection.execute(insert(schema.params[tool]).values(**preset["params"])).inserted_primary_key
            assert inserted is not None, "an INSERT of one row returns its key"
            connection.execute(
                insert(schema.presets).values(
                    name=preset["name"],
                    tool=tool,
                    description=preset["description"],
                    created_at=now,
                    updated_at=now,
                    **{PRESETS_PARAMS_ID.format(tool=tool): inserted[0]},
                )
            )
    except IntegrityError as exc:
        raise PresetNameTakenError(preset["name"]) from exc
