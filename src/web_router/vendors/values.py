"""Resolving one vendor call's values: its settings and credentials, and the injections they fill.

Apart from :mod:`web_router.vendors.caller` so the caller keeps only the
sending: which credentials a call needs, where their values come from, and
which of the vendor's injections the call carries are read off the plan here.
"""

from __future__ import annotations

from collections.abc import Mapping

from web_router.constants_vendors import MSG_VENDOR_MISSING_CREDENTIAL, MSG_VENDOR_SETTING_CHOICE
from web_router.exceptions import ConfigurationError
from web_router.vendors.credentials import CredentialSource
from web_router.vendors.plan import _Plan
from web_router.vendors.schema import ACCESS_TOKEN, Vendor, VendorInjection, placeholders


def needed(plan: _Plan) -> set[str]:
    """The credentials a call cannot be sent without: the endpoint's own list, else the vendor's required ones."""
    if plan.credentials is not None:
        return set(plan.credentials)
    return {name for name, spec in plan.vendor.auth.credentials.items() if spec.required}


def wants_token(plan: _Plan) -> bool:
    """Whether the call needs the vendor's OAuth token: always, unless its own credential list names none of
    the credentials the token is minted from (a key-only endpoint on an OAuth vendor)."""
    oauth = plan.vendor.auth.oauth2
    if oauth is None:
        return False
    if plan.credentials is None:
        return True
    minted_from = {name for template in oauth.templates() for name in placeholders(template)}
    return bool(set(plan.credentials) & minted_from)


def resolve(plan: _Plan, credentials: CredentialSource) -> dict[str, str]:
    """The call's settings and credentials, read from ``credentials``; a needed credential left unset raises."""
    vendor = plan.vendor
    required = needed(plan)
    resolved: dict[str, str] = {}
    for name, setting in vendor.settings.items():
        found = credentials.get(vendor.vendor, name, setting.env)
        value = found or setting.default
        if value is None:
            continue
        if setting.choices and value not in setting.choices:
            source = "/".join(setting.env) if found else "its default"
            raise ConfigurationError(
                MSG_VENDOR_SETTING_CHOICE.format(
                    vendor=vendor.vendor, name=name, choices=", ".join(setting.choices), value=value, source=source
                )
            )
        resolved[name] = value
    for name, cred in vendor.auth.credentials.items():
        value = credentials.get(vendor.vendor, name, cred.env)
        if value:
            resolved[name] = value
        elif name in required:
            raise missing_error(vendor, name)
    return resolved


def missing_error(vendor: Vendor, name: str) -> ConfigurationError:
    spec = vendor.auth.credentials.get(name)
    setting = vendor.settings.get(name)
    env = spec.env if spec else (setting.env if setting else [])
    what = spec.description if spec and spec.description else name
    return ConfigurationError(
        MSG_VENDOR_MISSING_CREDENTIAL.format(
            vendor=vendor.vendor, what=what, variables=" or ".join(env) or name, name=name
        )
    )


def render(vendor: Vendor, template: str, values: Mapping[str, str]) -> str:
    for name in placeholders(template):
        if name not in values:
            raise missing_error(vendor, name)
    return template.format_map(values)


def excluded_credentials(plan: _Plan) -> set[str]:
    """The vendor's credentials this endpoint's own list leaves out; none when it has no list."""
    if plan.credentials is None:
        return set()
    return set(plan.vendor.auth.credentials) - set(plan.credentials)


def vendor_headers(plan: _Plan, values: Mapping[str, str]) -> dict[str, str]:
    """The vendor-wide headers, rendered; one naming a credential the endpoint excludes is left off."""
    excluded = excluded_credentials(plan)
    vendor = plan.vendor
    return {
        name: render(vendor, value, values)
        for name, value in vendor.headers.items()
        if not placeholders(value) & excluded
    }


def basic_auth(plan: _Plan, values: Mapping[str, str]) -> tuple[str, str] | None:
    """HTTP Basic for this call, or None: none declared, or it names a credential the endpoint excludes."""
    basic = plan.vendor.auth.basic
    if basic is None or (placeholders(basic.username) | placeholders(basic.password)) & excluded_credentials(plan):
        return None
    return render(plan.vendor, basic.username, values), render(plan.vendor, basic.password, values)


def applied(plan: _Plan, values: Mapping[str, str]) -> list[VendorInjection]:
    """The injections this call carries.

    Left off: one naming a credential the endpoint's own list excludes (even when configured), an optional
    one missing a value, and one missing only credentials this call does not need -- or the access token,
    when this call mints none.
    """
    vendor = plan.vendor
    required = needed(plan)
    excluded = excluded_credentials(plan)
    skippable = set(vendor.auth.credentials) | (set() if wants_token(plan) else {ACCESS_TOKEN})
    carried = []
    for injection in vendor.auth.inject:
        names = placeholders(injection.value)
        if names & excluded or any(values.get(name) != value for name, value in (injection.when or {}).items()):
            continue
        absent = {name for name in names if name not in values}
        if absent and (injection.optional or not (absent & required or absent - skippable)):
            continue
        carried.append(injection)
    return carried
