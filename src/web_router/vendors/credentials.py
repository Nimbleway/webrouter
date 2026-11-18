"""Where vendor credentials come from.

A call asks a :class:`CredentialSource` for each value its vendor's file names
and never reads the environment itself. Today that is the process environment
(and the ``.env`` the MCP server loads into it), or values passed to the
router; a hosted vault holding OAuth apps for the user is one more source, and
nothing that sends a call has to change to use it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from web_router.vendors.schema import Vendor, VendorEndpoint
from web_router.vendors.types import VendorSummary


@runtime_checkable
class CredentialSource(Protocol):
    """Answers "what is ``name`` for ``vendor``?", or ``None`` when it does not know.

    ``env`` is the variables the vendor's file lists for that value, in order,
    for the sources that read the environment; others may ignore it.
    """

    def get(self, vendor: str, name: str, env: Sequence[str]) -> str | None: ...


class EnvCredentials:
    """Values from the process environment: the first of the file's variables that is set and not blank."""

    def get(self, vendor: str, name: str, env: Sequence[str]) -> str | None:
        for variable in env:
            value = (os.environ.get(variable) or "").strip()
            if value:
                return value
        return None


class MappingCredentials:
    """Values passed in code, as ``{vendor: {name: value}}``."""

    def __init__(self, values: Mapping[str, Mapping[str, str]]) -> None:
        self._values = {vendor: dict(names) for vendor, names in values.items()}

    def get(self, vendor: str, name: str, env: Sequence[str]) -> str | None:
        value = (self._values.get(vendor, {}).get(name) or "").strip()
        return value or None


class ChainedCredentials:
    """The first source that knows a value wins."""

    def __init__(self, *sources: CredentialSource) -> None:
        self.sources = sources

    def get(self, vendor: str, name: str, env: Sequence[str]) -> str | None:
        for source in self.sources:
            value = source.get(vendor, name, env)
            if value:
                return value
        return None


def credential_source(
    vendor_credentials: CredentialSource | Mapping[str, Mapping[str, str]] | None,
) -> CredentialSource:
    """What a router built with ``vendor_credentials=`` reads: those values first, then the environment."""
    if vendor_credentials is None:
        return EnvCredentials()
    # Mapping first: a dict has a get() too, so it would pass for a source.
    if isinstance(vendor_credentials, Mapping):
        return ChainedCredentials(MappingCredentials(vendor_credentials), EnvCredentials())
    return vendor_credentials


def missing_credentials(vendor: Vendor, source: CredentialSource, endpoint: VendorEndpoint | None = None) -> list[str]:
    """The first-choice variable of each credential a call needs that ``source`` does not hold.

    What a call needs is the caller's rule: the endpoint's own credential list when it has one, else every
    credential ``vendor`` requires.
    """
    if endpoint is not None and endpoint.credentials is not None:
        needed = set(endpoint.credentials)
    else:
        needed = {name for name, spec in vendor.auth.credentials.items() if spec.required}
    return [
        spec.env[0]
        for name, spec in vendor.auth.credentials.items()
        if name in needed and not source.get(vendor.vendor, name, spec.env)
    ]


def summarize(vendor: Vendor, source: CredentialSource) -> VendorSummary:
    """One vendor's listing row, configured or not according to ``source``."""
    missing = missing_credentials(vendor, source)
    return VendorSummary(
        vendor=vendor.vendor,
        name=vendor.name,
        category=vendor.category,
        summary=vendor.summary,
        docs_url=vendor.docs_url,
        endpoint_count=len(vendor.endpoints),
        configured=not missing,
        missing=missing,
    )
