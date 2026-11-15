"""What every model of the vendor catalog's file format shares: the strict base and the placeholder rule.

Apart from :mod:`web_router.vendors.schema` so the auth models
(:mod:`web_router.vendors.schema_auth`) and the vendor model can both build
on it without importing each other.
"""

from __future__ import annotations

import string
from typing import Literal

from pydantic import BaseModel, ConfigDict

ACCESS_TOKEN = "access_token"
"""The placeholder an ``oauth2`` block fills with the access token it minted."""

ParamLocation = Literal["path", "query", "body", "header"]
ParamType = Literal["string", "integer", "number", "boolean", "array", "object"]
EndpointKind = Literal["read", "write"]
HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]

_FORMATTER = string.Formatter()


def placeholders(template: str) -> set[str]:
    """The ``{names}`` a template string refers to."""
    return {field for _, field, _, _ in _FORMATTER.parse(template) if field}


def _one_line(value: str | None) -> str | None:
    """Refuse a line break: these fields are printed into ``VENDORS.md`` cells and ``.env.example`` comments."""
    if value is not None and ("\n" in value or "\r" in value):
        raise ValueError(f"must be one line: {value!r}")
    return value


class _Strict(BaseModel):
    # A key the format does not define is a typo in a catalog file, never something to ignore.
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
