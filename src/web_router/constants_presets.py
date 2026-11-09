"""Presets' names, storage settings and messages, kept apart so ``constants.py`` stops growing."""

from __future__ import annotations

PRESET_NAME_PATTERN = r"^[A-Za-z0-9_]+$"
"""ASCII letters, digits and ``_``: safe as a file name, a URL path segment and a JSON key as it is."""

PRESET_NAME_MAX_LENGTH = 100
PRESET_DESCRIPTION_MAX_LENGTH = 2000
# Postgres text refuses NUL: refused when the preset is made, not when a store writes it.
PRESET_DESCRIPTION_PATTERN = r"^[^\x00]*$"

PRESET_NAME_DESCRIPTION = (
    "The preset's name: ASCII letters, digits and `_` only, like `supplier_research`. Unique in its store and "
    "case-sensitive: `Report` and `report` are two presets. Fixed once created."
)
PRESET_PARAMS_DESCRIPTION = (
    "The method's own optional arguments, as it takes them, less the per-call input. Only those set are saved; a "
    "call that sets one itself overrides it."
)

PRESET_FILE_JSON = ".json"
PRESET_FILE_YAML = frozenset({".yaml", ".yml"})
PRESET_FILE_ENCODING = "utf-8"
PRESET_FILE_INDENT = 2

EXTRA_YAML = "yaml"
EXTRA_SQL = "sql"
PRESET_YAML_READER = "yaml presets"
PRESET_SQL_STORE = "sql presets"

PRESETS_TABLE = "web_router_presets"
PRESETS_PARAMS_TABLE = "web_router_{tool}_params"
PRESETS_PARAMS_ID = "{tool}_params_id"
PRESETS_VERSION_TABLE = "web_router_presets_alembic_version"
"""Alembic's own table, named for us so a database that already runs Alembic for its app keeps both apart."""

PRESETS_UPGRADE_COMMAND = "python -m web_router.presets_sql upgrade <url>"

MSG_PRESET_UNKNOWN_PARAM = "`{key}` is not {a} {tool} param; {tool} takes: {takes}"
MSG_PRESET_NOT_FOUND = "No preset named '{name}'"
MSG_PRESET_OF_ANOTHER_TOOL = "Preset '{name}' runs {tool}"
MSG_PRESET_NAME_TAKEN = "You already have a preset named '{name}'"
MSG_PRESET_UNREADABLE = "{where} cannot be read as presets: {detail}"
MSG_PRESET_DEFINED_TWICE = "preset '{name}' is also defined in {other}"
MSG_PRESET_STORE_OUTDATED = (
    "The presets database schema is at revision {current}, behind this version's {head}. Run `{command}` "
    "(or web_router.presets_sql.upgrade(url)) to bring it up to date."
)
PRESET_STORE_EMPTY_REVISION = "none"
PRESETS_CLI_PROG = "python -m web_router.presets_sql"
PRESETS_CLI_DESCRIPTION = "Manage the schema of a SqlPresetStore database."
PRESETS_CLI_UPGRADE = "upgrade"
PRESETS_CLI_UPGRADE_HELP = "Bring the presets table up to this version's schema."
PRESETS_CLI_URL_HELP = "SQLAlchemy database URL, e.g. sqlite:///presets.db or postgresql+psycopg://user@host/db"
MSG_PRESETS_UPGRADED = "presets schema is at revision {revision}"
PRESETS_SQL_WHERE = "table {table}, preset '{name}'"

# The managed presets' limits, for the params both have (presets/limits.py).
PRESET_TEXT_PATTERN = r"^[^\x00]*$"
PRESET_URL_PATTERN = r"^[Hh][Tt][Tt][Pp][Ss]?://\S+$"
PRESET_DOMAINS_MAX = 50
PRESET_MAX_RESULTS_MAX = 100
PRESET_MAX_STEPS_MAX = 10_000
PRESET_SCHEMA_MAX_DEPTH = 32
PRESET_SCHEMA_MAX_BYTES = 64 * 1024
MSG_PRESET_NUL = "holds a NUL character"
MSG_PRESET_URL = "must be an absolute http or https URL"
MSG_PRESET_SCHEMA_TOO_DEEP = "nested deeper than {levels} levels"
MSG_PRESET_SCHEMA_UNENCODABLE = "holds NaN or Infinity, or a lone UTF-16 surrogate, which JSON cannot represent"
MSG_PRESET_SCHEMA_TOO_LARGE = "larger than {kb} KB as JSON"
LOG_PRESET_SKIPPED = "Left a preset out of the listing: %s"

PRESET_SECRET_PARAMS = frozenset({"provider_options"})
"""Params whose values may hold a vendor credential: shown by key only, with each value as PRESET_REDACTED."""
PRESET_REDACTED = "[redacted]"
PRESET_PROBLEM = "{where}: {kind}"
"""One validation problem as an unreadable preset reports it: where and what kind, never the value."""
PRESET_PROBLEM_SEPARATOR = "; "
PRESET_PROBLEM_ROOT = "(preset)"
PRESET_PARSE_PROBLEM = "{msg} at line {line}, column {column}"
