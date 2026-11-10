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
    ENV_BY_PROVIDER,
    ENV_JINA_API_KEY,
    EXTRACT_ENV_BY_PROVIDER,
    RESEARCH_ENV_BY_PROVIDER,
    TEMPLATE_ENV_BY_PROVIDER,
)
from web_router.exceptions import ConfigurationError
from web_router.vendors.catalog import get_catalog
from web_router.vendors.schema import Vendor

ALL_ENV_TABLES: tuple[Mapping[str, str | tuple[str, ...] | None], ...] = (
    ENV_BY_PROVIDER,
    EXTRACT_ENV_BY_PROVIDER,
    TEMPLATE_ENV_BY_PROVIDER,
    ANSWER_ENV_BY_PROVIDER,
    RESEARCH_ENV_BY_PROVIDER,
    BROWSER_ENV_BY_PROVIDER,
    BROWSE_ENV_BY_PROVIDER,
)
"""Every method's ``*_ENV_BY_PROVIDER`` table: what a file may set, and, in the tools, what would unlock more."""

_OPTIONAL_KEYS = (ENV_JINA_API_KEY,)
"""Keys a keyless backend takes only to raise its rate limit, so no table names them: Jina Reader's."""


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
    """Every credential variable: what :data:`ALL_ENV_TABLES` names, the optional keys none does, and each vendor's.

    A vendor's settings (its region, its API version) are taken too: they are
    read from the same place, and a ``.env`` that sets ``DD_SITE`` beside
    ``DD_API_KEY`` means both.
    """
    named = {name for table in ALL_ENV_TABLES for env in table.values() for name in env_names(env)}
    vendors = {var for vendor in _vendors() for var in vendor.credential_variables()}
    return frozenset(named | set(_OPTIONAL_KEYS) | vendors)


def _vendors() -> list[Vendor]:
    """The vendor catalog, or none when a bad ``WEB_ROUTER_VENDOR_CATALOG`` cannot load: the server still starts,
    and the vendor tools report the bad catalog when they are called."""
    try:
        return get_catalog().all()
    except ConfigurationError:
        return []


@dataclass(frozen=True)
class EnvLoad:
    """One file's credentials applied: the names it set, and the ones it left to a value already set."""

    path: Path
    loaded: tuple[str, ...]
    already_set: tuple[str, ...]
    remembered: Path | None = None


def apply_credentials(path: Path, values: dict[str, str], *, remembered: Path | None = None) -> EnvLoad:
    """Set each of ``values`` still unset; a credential of several variables only as a whole.

    Set-and-empty counts as unset, as the registry reads it. A file that holds
    only part of a credential applies none of it: a lone Bright Data token
    would otherwise shut out a later file's complete token and zone. It is
    neither loaded nor already set, so the report leaves it out. A variable
    whose partner is already set is kept out too: a project's
    ``BRIGHTDATA_ZONE`` beside the user's own token would send the user's
    account through a zone the project chose. Each catalog vendor follows the
    same rule: see :func:`_vendor_rule`.
    """
    partial = {name for group in _GROUPS if not group <= values.keys() for name in group & values.keys()}
    taken = {name for name in values if os.environ.get(name)}
    taken |= {name for group in _GROUPS for name in group if name in values and any(os.environ.get(n) for n in group)}
    vendor_taken, vendor_partial = _vendor_rule(values)
    taken |= vendor_taken
    partial |= vendor_partial
    loaded = tuple(name for name in values if name not in taken | partial)
    for name in loaded:
        os.environ[name] = values[name]
    return EnvLoad(path, loaded, tuple(name for name in values if name in taken - partial), remembered)


def _vendor_rule(values: Mapping[str, str]) -> tuple[set[str], set[str]]:
    """The file's vendor variables to keep out, as ``(taken, partial)``, one vendor at a time.

    A vendor any of whose credentials the user already set keeps the file's
    whole set for that vendor out, settings included: a project's ``DD_SITE``
    would otherwise send the user's own Datadog keys to the site the project
    chose. Otherwise its credentials load only as a whole: every required
    credential under one of its names. A file holding only a vendor's
    settings loads them.
    """
    taken: set[str] = set()
    partial: set[str] = set()
    for vendor in _vendors():
        credentials = [frozenset(spec.env) for spec in vendor.auth.credentials.values()]
        required = [frozenset(spec.env) for spec in vendor.auth.credentials.values() if spec.required]
        settings = {name for setting in vendor.settings.values() for name in setting.env}
        in_file = ({name for names in credentials for name in names} | settings) & values.keys()
        if not in_file:
            continue
        if any(os.environ.get(name) for names in credentials for name in names):
            taken |= in_file
        elif in_file - settings and not all(names & values.keys() for names in required):
            partial |= in_file
    return taken, partial
