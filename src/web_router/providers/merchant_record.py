"""What the ``merchant`` backend decides from a registry record, with no network.

Pure functions over decoded JSON, so the sync and async providers share one reading of the registry and the tests need
no transport: read the pointer, pick the one route and check where it points, gate the options, describe the input.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from web_router.constants_merchant import (
    EVIDENCE_PASS,
    GATES,
    MSG_HOST_REFUSED,
    MSG_NEEDS_PROFILE,
    MSG_REGISTRY_INVALID,
    PLATFORM_HOST_SUFFIXES,
    PROVIDER_MERCHANT,
    REGISTRY_SCHEMA_VERSION,
    ROUTE_PROTOCOL,
    ROUTE_PROVIDER,
    ROUTE_SCHEME,
    SNAPSHOT_ID,
    TEST_SEARCH,
)
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.exceptions_merchant import MerchantNotValidatedError, MerchantRouteError
from web_router.types_errors import ERROR_KIND_UPSTREAM
from web_router.types_merchant import MerchantInputs

__all__ = ["Plan", "gate", "host_allowed", "input_schema", "plan_for", "read_pointer"]


@dataclass(frozen=True)
class Plan:
    """Everything the store call needs, decided before any request goes to the store."""

    domain: str
    snapshot_id: str
    endpoint: str
    protocol_version: str | None
    needs_profile: bool
    evidence: dict[str, str]
    observed_at: str | None
    production_ready: bool
    search_term: str | None = None
    tests: dict[str, str] = field(default_factory=dict)


def _invalid(reason: str) -> ProviderError:
    return ProviderError(PROVIDER_MERCHANT, MSG_REGISTRY_INVALID.format(reason=reason), kind=ERROR_KIND_UPSTREAM)


def read_pointer(document: Any) -> str:
    """The snapshot id out of ``latest.json``, after the checks that stop a bad pointer reaching a path."""
    if not isinstance(document, dict) or document.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise _invalid(f"schema_version is not {REGISTRY_SCHEMA_VERSION!r}")
    snapshot_id = document.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not SNAPSHOT_ID.match(snapshot_id):
        raise _invalid("snapshot_id is missing or not a hex id")
    return snapshot_id


def host_allowed(host: str, domain: str) -> bool:
    """Whether a route's host may be called for ``domain``: the domain, a subdomain, or a trusted platform host.

    An IP literal is never allowed, nor is ``localhost``: the registry names the hosts this client sends the caller's
    agent profile URL to, so it may only name stores' own hosts and the platforms that run them.
    """
    host = host.lower().rstrip(".")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        return False
    if host == "localhost" or "." not in host:
        return False
    bare = domain.removeprefix("www.")
    return host in (domain, bare) or host.endswith(f".{bare}") or host.endswith(PLATFORM_HOST_SUFFIXES)


def _runnable(route: Any) -> bool:
    if (
        not isinstance(route, dict)
        or route.get("provider") != ROUTE_PROVIDER
        or route.get("protocol") != ROUTE_PROTOCOL
    ):
        return False
    endpoint = route.get("endpoint")
    # A plain-http endpoint is an insecure declaration, never a default to run; a credential in the URL is refused too.
    if not isinstance(endpoint, str):
        return False
    try:
        parts = urlsplit(endpoint)
        host = parts.hostname
    except ValueError:  # e.g. an unclosed bracket in the host
        return False
    # "@" in the authority means user info; ``username`` alone misses ``https://:secret@host``.
    return parts.scheme == ROUTE_SCHEME and bool(host) and "@" not in parts.netloc and not parts.query


def plan_for(record: Any, domain: str, snapshot_id: str) -> Plan:
    """Turn a domain record into the call to make, or raise why this store is not served."""
    if not isinstance(record, dict) or record.get("snapshot_id") != snapshot_id or record.get("domain") != domain:
        raise _invalid("the domain record is not the one asked for, or is from another snapshot")

    def refuse(reason: str) -> MerchantRouteError:
        return MerchantRouteError(PROVIDER_MERCHANT, domain, snapshot_id, reason, ROUTE_PROVIDER)

    commerce = record.get("commerce_evidence")
    if record.get("quarantined"):
        raise refuse("the domain is quarantined in the registry")
    if (
        not isinstance(commerce, dict)
        or not commerce.get("reached")
        or not commerce.get("endpoint_matches_recorded_route")
    ):
        raise refuse("no profile-backed test reached this route")
    if commerce.get("real_catalogue") is not True:
        raise refuse("the catalogue is template or demo data, or was not checked")
    routes = [route for route in record.get("routes") or [] if _runnable(route)]
    if len(routes) != 1:
        raise refuse("no https route of that kind" if not routes else f"{len(routes)} candidate routes, cannot choose")
    route = routes[0]
    host = urlsplit(route["endpoint"]).hostname or ""
    if not host_allowed(host, domain):
        raise refuse(MSG_HOST_REFUSED.format(host=host, domain=domain))
    if route["endpoint"] != commerce.get("endpoint"):
        raise refuse("the tested endpoint is not the registry route")
    raw_tests = commerce.get("tests")
    tests = {str(name): str(status) for name, status in raw_tests.items()} if isinstance(raw_tests, dict) else {}
    if tests.get(TEST_SEARCH) != EVIDENCE_PASS:
        raise refuse(f"keyword search is recorded as {tests.get(TEST_SEARCH)!r}, not a pass")
    capability = _first(record.get("capabilities"))
    term = commerce.get("search_term_that_worked")
    return Plan(
        domain=domain,
        snapshot_id=snapshot_id,
        endpoint=str(route["endpoint"]),
        protocol_version=route.get("protocol_version"),
        needs_profile=bool(commerce.get("needs_agent_profile")),
        evidence=tests,
        observed_at=commerce.get("observed_date"),
        production_ready=bool(capability.get("production_ready")) if capability else False,
        search_term=term if isinstance(term, str) and term.strip() else None,
        tests=tests,
    )


def _first(items: Any) -> dict[str, Any] | None:
    return items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else None


def gate(plan: Plan, inputs: MerchantInputs, profile_url: str | None) -> None:
    """Refuse, before anything is sent to the store, what the registry has no passing test for."""
    by_test: dict[str, set[str]] = {}
    for option in inputs.specified():
        test = GATES.get(option)
        if test is not None:
            by_test.setdefault(test, set()).add(option)
    for test, options in sorted(by_test.items()):
        status = plan.tests.get(test, "untested")
        if status != EVIDENCE_PASS:
            raise MerchantNotValidatedError(PROVIDER_MERCHANT, plan.domain, options, test, status)
    if plan.needs_profile and not profile_url:
        raise ConfigurationError(MSG_NEEDS_PROFILE.format(domain=plan.domain))


def input_schema(plan: Plan) -> dict[str, Any]:
    """The source's input schema: an option appears only where the registry has a passing test for it."""
    properties = {
        name: schema
        for name, schema in MerchantInputs.model_json_schema()["properties"].items()
        if GATES.get(name) is None or plan.tests.get(GATES[name]) == EVIDENCE_PASS
    }
    return {"type": "object", "properties": properties, "required": ["query"], "additionalProperties": False}
