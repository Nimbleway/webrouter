"""The limits a preset holds a param to beyond its method's own, shared with Nimble's managed presets.

A preset is kept, often in a database, and read back by other code later, so
it refuses what a store cannot keep or a backend would refuse anyway: a NUL in
any text (Postgres text refuses it), more than 100 search results (past that
the backends that cap it refuse), a domain list past 50 names, an empty or
oversized output schema. The method itself is unchanged: a direct call takes
what it always took.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

from pydantic import AfterValidator, Field, StringConstraints

from web_router.constants_presets import (
    MSG_PRESET_NUL,
    MSG_PRESET_SCHEMA_TOO_DEEP,
    MSG_PRESET_SCHEMA_TOO_LARGE,
    MSG_PRESET_SCHEMA_UNENCODABLE,
    MSG_PRESET_URL,
    PRESET_DOMAINS_MAX,
    PRESET_MAX_RESULTS_MAX,
    PRESET_MAX_STEPS_MAX,
    PRESET_SCHEMA_MAX_BYTES,
    PRESET_SCHEMA_MAX_DEPTH,
    PRESET_TEXT_PATTERN,
    PRESET_URL_PATTERN,
)

Text = Annotated[str, StringConstraints(pattern=PRESET_TEXT_PATTERN)]
_SearchDomains = Annotated[list[Text], Field(max_length=PRESET_DOMAINS_MAX)]
_Domains = Annotated[
    list[Annotated[str, StringConstraints(min_length=1, pattern=PRESET_TEXT_PATTERN)]],
    Field(min_length=1, max_length=PRESET_DOMAINS_MAX),
]


def _deeper_than(value: Any, levels: int) -> bool:
    if not isinstance(value, (dict, list)):
        return False
    return levels == 0 or any(
        _deeper_than(item, levels - 1) for item in (value.values() if isinstance(value, dict) else value)
    )


def _schema_kept_as_json(schema: dict[str, Any]) -> dict[str, Any]:
    if _deeper_than(schema, PRESET_SCHEMA_MAX_DEPTH):
        raise ValueError(MSG_PRESET_SCHEMA_TOO_DEEP.format(levels=PRESET_SCHEMA_MAX_DEPTH))
    try:
        encoded = json.dumps(schema, allow_nan=False, ensure_ascii=False).encode()
    except ValueError as exc:
        raise ValueError(MSG_PRESET_SCHEMA_UNENCODABLE) from exc
    if len(encoded) > PRESET_SCHEMA_MAX_BYTES:
        raise ValueError(MSG_PRESET_SCHEMA_TOO_LARGE.format(kb=PRESET_SCHEMA_MAX_BYTES // 1024))
    return schema


def _url(url: str) -> str:
    if "\x00" in url:
        raise ValueError(MSG_PRESET_NUL)
    # fullmatch, not match: `$` alone also matches before a trailing newline, which would then be sent.
    if not re.fullmatch(PRESET_URL_PATTERN, url):
        raise ValueError(MSG_PRESET_URL)
    return url


# A schema with nothing in it would ask one backend for structure and another for prose.
_Schema = Annotated[dict[str, Any], Field(min_length=1), AfterValidator(_schema_kept_as_json)]

LIMITS: dict[tuple[str, str], Any] = {
    ("search", "max_results"): Annotated[int, Field(ge=1, le=PRESET_MAX_RESULTS_MAX)],
    ("search", "include_domains"): _SearchDomains,
    ("search", "exclude_domains"): _SearchDomains,
    ("search", "country"): Text,
    ("search", "locale"): Text,
    ("research", "output_schema"): _Schema,
    ("research", "include_domains"): _Domains,
    ("research", "exclude_domains"): _Domains,
    ("browse", "start_url"): Annotated[str, AfterValidator(_url)],
    ("browse", "output_schema"): _Schema,
    ("browse", "max_steps"): Annotated[int, Field(ge=1, le=PRESET_MAX_STEPS_MAX)],
    ("browse", "allowed_domains"): _Domains,
}
"""The managed presets' own types for the params both have, keyed by (tool, param); the method's type elsewhere."""
