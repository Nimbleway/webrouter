"""The MCP server's preset tools, their text and the variable that picks the store. Imported by name from here.

The examples are the managed web-gateway presets' own, adjusted to the
library's methods and params: ``eu_semiconductor_news``, ``page_text``,
``supplier_profile`` and ``page_heading``.
"""

from __future__ import annotations

from typing import Any

from web_router.constants_presets import PRESET_DESCRIPTION_MAX_LENGTH, PRESET_NAME_MAX_LENGTH, PRESET_NAME_PATTERN

ENV_MCP_PRESETS = "WEB_ROUTER_PRESETS"
"""Where the server keeps presets: a directory (FilePresetStore) or a database URL (SqlPresetStore).

Unset, presets live in the server's memory and end with it. Read once from the
process environment at startup, never from a ``.env`` file.
"""
MCP_PRESETS_URL_MARK = "://"
"""What tells a database URL from a directory: ``sqlite:///presets.db``, ``postgresql+psycopg://...``."""
MCP_MSG_PRESETS_EMPTY = "{env_var} is set but empty: set it to a directory or a database URL, or unset it."

MCP_TOOL_LIST_PRESETS = "list_presets"
MCP_TOOL_GET_PRESET = "get_preset"
MCP_TOOL_CREATE_PRESET = "create_preset"
MCP_PRESET_TOOLS = (MCP_TOOL_LIST_PRESETS, MCP_TOOL_GET_PRESET, MCP_TOOL_CREATE_PRESET)

MCP_ARG_PRESET = "preset"
MCP_PRESET_PROPERTY: dict[str, Any] = {
    "type": "string",
    "description": "A preset of this tool, by name, as `list_presets` shows them: its params, under any you pass "
    "here. A preset of another tool is refused.",
}
"""The ``preset`` argument each method tool takes."""

MCP_PRESET_METHOD_OF: dict[str, str] = {
    "search": "search",
    "extract": "extract",
    "answer": "answer",
    "research": "research",
    "browse": "browse",
    "run_template": "run_template",
    "browser_open": "open_browser",
}
"""Each MCP tool that takes ``preset``, and the library method -- a preset's ``tool`` -- it runs."""

MCP_PRESET_SERVER_WAITS: frozenset[str] = frozenset({"timeout"})
"""A research or browse preset's ``timeout`` is the library's wait; over MCP the server's own wait applies instead."""

MCP_MSG_PRESET_NOT_OVER_MCP = (
    "Preset '{name}' sets {keys}, which `{tool}` does not take over MCP; use a preset without {them}, or call "
    "the library directly."
)

_PRESET_EXAMPLES = (
    "Examples: `eu_semiconductor_news` (search: country DE, locale en, max_results 20, search_depth fast, "
    "include_domains reuters.com, eetimes.eu, semiengineering.com, exclude_domains reddit.com), `page_text` "
    "(extract: format text), `supplier_profile` (research: effort high, an output_schema "
    "with company, headquarters_country, employees and main_products, include_domains sec.gov, "
    "companieshouse.gov.uk, opencorporates.com, exclude_domains wikipedia.org), `page_heading` (browse: "
    "max_steps 3, allowed_domains example.com, iana.org, an output_schema with heading)."
)

MCP_DESC_LIST_PRESETS = (
    "List the saved presets, of every tool, in no particular order: each names its tool, the params it saves "
    "and what it is for. Pass a preset's name as `preset` to that tool to run with its params."
)
MCP_DESC_GET_PRESET = "Read one preset by name: its tool and the params it saves, only those it sets."
MCP_DESC_CREATE_PRESET = (
    "Save a tool's params under a name, so a call of that tool with `preset` runs with them. `tool` is the "
    "library method: search, extract, answer, research, browse, run_template or open_browser (the "
    "`browser_open` tool). `params` are that method's own optional arguments, as it takes them, without its "
    "input -- the query, the URLs, the task, the template or research's records: a preset never stores the "
    "input, and none a tool here does not take: no `provider_options`, no `profile`. Names use letters, "
    "digits and `_` only, are case-sensitive and fixed once created; a taken name is "
    "refused, and nothing is ever overwritten. " + _PRESET_EXAMPLES
)

MCP_PRESET_TOOL_PROPERTIES: dict[str, dict[str, dict[str, Any]]] = {
    MCP_TOOL_LIST_PRESETS: {},
    MCP_TOOL_GET_PRESET: {"name": {"type": "string", "description": "The preset's name."}},
    MCP_TOOL_CREATE_PRESET: {
        "name": {
            "type": "string",
            "pattern": PRESET_NAME_PATTERN,
            "maxLength": PRESET_NAME_MAX_LENGTH,
            "description": "Letters, digits and `_`, like `eu_semiconductor_news`.",
        },
        "tool": {
            "type": "string",
            "enum": list(MCP_PRESET_METHOD_OF.values()),
            "description": "The library method the preset runs.",
        },
        "params": {
            "type": "object",
            "description": "The method's optional arguments to save, e.g. "
            '{"search_depth": "fast", "max_results": 20, "include_domains": ["reuters.com"]}.',
        },
        "description": {
            "type": "string",
            "maxLength": PRESET_DESCRIPTION_MAX_LENGTH,
            "description": "What the preset is for.",
        },
    },
}
MCP_PRESET_TOOL_REQUIRED: dict[str, tuple[str, ...]] = {
    MCP_TOOL_LIST_PRESETS: (),
    MCP_TOOL_GET_PRESET: ("name",),
    MCP_TOOL_CREATE_PRESET: ("name", "tool"),
}
MCP_PRESET_TOOL_DESCRIPTIONS: dict[str, str] = {
    MCP_TOOL_LIST_PRESETS: MCP_DESC_LIST_PRESETS,
    MCP_TOOL_GET_PRESET: MCP_DESC_GET_PRESET,
    MCP_TOOL_CREATE_PRESET: MCP_DESC_CREATE_PRESET,
}
