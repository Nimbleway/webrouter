"""Which variables are provider credentials, and how a file's values join the environment.

Read by :mod:`web_router.mcp.env`, which finds the files, and by the tools,
which name what would unlock more.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from web_router.constants import (
    ANSWER_ENV_BY_PROVIDER,
    BROWSE_ENV_BY_PROVIDER,
    BROWSER_ENV_BY_PROVIDER,
    ENV_AGENTCORE_GATEWAY_URL,
    ENV_BY_PROVIDER,
    ENV_JINA_API_KEY,
    EXTRACT_ENV_BY_PROVIDER,
    RESEARCH_ENV_BY_PROVIDER,
    TEMPLATE_ENV_BY_PROVIDER,
)
from web_router.constants_mcp import MCP_MSG_ENV_ONLY

ALL_ENV_TABLES: tuple[Mapping[str, str | tuple[str, ...] | None], ...] = (
    ENV_BY_PROVIDER,
    EXTRACT_ENV_BY_PROVIDER,
    TEMPLATE_ENV_BY_PROVIDER,
    ANSWER_ENV_BY_PROVIDER,
    RESEARCH_ENV_BY_PROVIDER,
    BROWSER_ENV_BY_PROVIDER,
    BROWSE_ENV_BY_PROVIDER,
)
"""Every method's ``*_ENV_BY_PROVIDER`` table: the credentials read, and, in the tools, what would unlock more."""

_OPTIONAL_KEYS = (ENV_JINA_API_KEY,)
"""Keys a keyless backend takes only to raise its rate limit, so no table names them: Jina Reader's."""


ENV_ONLY: tuple[str, ...] = (ENV_AGENTCORE_GATEWAY_URL,)
"""Variables read from the environment only, never from a file: each names where requests go, not a key.

``agentcore`` signs every request with the user's AWS credential, so its
gateway comes only from the user's own environment or a ``gateway_url`` in code.
"""

_GROUPS: tuple[frozenset[str], ...] = tuple(
    {frozenset(env) for table in ALL_ENV_TABLES for env in table.values() if isinstance(env, tuple) and len(env) > 1}
)
"""Credentials of several variables -- Oxylabs' username and password, Bright Data's token and zone."""


def env_names(env: str | tuple[str, ...] | None) -> tuple[str, ...]:
    """The variable names one ``*_ENV_BY_PROVIDER`` entry holds: search's a name or none, every other a tuple."""
    if env is None:
        return ()
    return (env,) if isinstance(env, str) else env


def credential_variables() -> frozenset[str]:
    """Every provider credential variable: what :data:`ALL_ENV_TABLES` names, and the optional keys none does."""
    named = {name for table in ALL_ENV_TABLES for env in table.values() for name in env_names(env)}
    return frozenset(named | set(_OPTIONAL_KEYS))


def file_variables() -> frozenset[str]:
    """The credentials a file may set: every one but :data:`ENV_ONLY`."""
    return credential_variables() - set(ENV_ONLY)


def env_only_notes(names: tuple[str, ...]) -> list[str]:
    """The note for the :data:`ENV_ONLY` variables a file set, which were left out: none when it set none."""
    return [MCP_MSG_ENV_ONLY.format(names=", ".join(names))] if names else []


@dataclass(frozen=True)
class EnvLoad:
    """One file's credentials applied: the names it set, and the ones it left to a value already set."""

    path: Path
    loaded: tuple[str, ...]
    already_set: tuple[str, ...]
    remembered: Path | None = None
    env_only: tuple[str, ...] = ()
    """The :data:`ENV_ONLY` variables the file set, which were left out."""

    def notes(self) -> list[str]:
        return env_only_notes(self.env_only)


def apply_credentials(
    path: Path, values: dict[str, str], *, remembered: Path | None = None, env_only: tuple[str, ...] = ()
) -> EnvLoad:
    """Set each of ``values`` still unset; a credential of several variables only as a whole.

    Set-and-empty counts as unset, as the registry reads it. A file that holds
    only part of a credential applies none of it: a lone Bright Data token
    would otherwise shut out a later file's complete token and zone. It is
    neither loaded nor already set, so the report leaves it out. A variable
    whose partner is already set is kept out too: a project's
    ``BRIGHTDATA_ZONE`` beside the user's own token would send the user's
    account through a zone the project chose.
    """
    partial = {name for group in _GROUPS if not group <= values.keys() for name in group & values.keys()}
    taken = {name for name in values if os.environ.get(name)}
    taken |= {name for group in _GROUPS for name in group if name in values and any(os.environ.get(n) for n in group)}
    loaded = tuple(name for name in values if name not in taken | partial)
    for name in loaded:
        os.environ[name] = values[name]
    already_set = tuple(name for name in values if name in taken - partial)
    return EnvLoad(path, loaded, already_set, remembered, env_only)
