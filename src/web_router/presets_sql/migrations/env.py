"""Alembic's entry point for the presets schema.

Run through :func:`web_router.presets_sql.upgrade`, which hands over an open
connection: there is no ``alembic.ini`` and no URL in any config file, so the
database's credentials stay with the caller.
"""

from __future__ import annotations

from alembic import context

from web_router.constants_presets import PRESETS_VERSION_TABLE
from web_router.presets_sql._sql import CONNECTION

context.configure(connection=context.config.attributes[CONNECTION], version_table=PRESETS_VERSION_TABLE)
with context.begin_transaction():
    context.run_migrations()
