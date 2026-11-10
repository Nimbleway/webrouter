"""The vendor catalog's file format, as strict pydantic models.

One JSON file per vendor, under ``web_router/data/vendors/``. A file says
where the vendor's API lives, how a call authenticates, and which endpoints
are worth naming -- each with its inputs, whether it reads or writes, the page
of the vendor's docs it was written from, and how it was checked.

Every vendor-specific fact lives here as data. The code that sends a call
(:mod:`web_router.vendors.caller`) knows nothing about any one vendor: it
renders the ``auth`` block, places the parameters where the endpoint says,
and forwards the vendor's answer untouched. Adding a vendor is adding a file.

Strings in ``base_url``, ``headers`` and ``auth`` may name ``{placeholders}``:
a credential, a setting, or ``access_token`` -- the token an ``oauth2`` block
mints. The models check every placeholder resolves, so a typo fails when the
catalog loads rather than as a 401 on the first call.
"""

from __future__ import annotations

import re
import string
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from web_router.constants_vendors import (
    MSG_VENDOR_CATALOG_ACCESS_TOKEN,
    MSG_VENDOR_CATALOG_BASE_URL_REF,
    MSG_VENDOR_CATALOG_BODY_ON_GET,
    MSG_VENDOR_CATALOG_DUPLICATE_ENDPOINT,
    MSG_VENDOR_CATALOG_ENDPOINT_PREFIX,
    MSG_VENDOR_CATALOG_PATH_PARAM,
    MSG_VENDOR_CATALOG_PATH_UNUSED,
    MSG_VENDOR_CATALOG_SCHEME,
    MSG_VENDOR_CATALOG_SETTING_CHOICES,
    MSG_VENDOR_CATALOG_UNKNOWN_REF,
    MSG_VENDOR_OAUTH_LITERAL,
    VENDOR_CATEGORIES,
    VENDOR_ENDPOINT_ID_PATTERN,
    VENDOR_ENV_PATTERN,
    VENDOR_ID_PATTERN,
)

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


def _env_names(value: list[str]) -> list[str]:
    for name in value:
        if not re.match(VENDOR_ENV_PATTERN, name):
            raise ValueError(f"{name!r} is not an environment variable name")
    return value


class VendorSetting(_Strict):
    """A non-secret value a vendor's calls need: a region, an account id, an API version."""

    env: list[str] = Field(min_length=1, description="Variables read in order; the first that is set wins.")
    default: str | None = Field(default=None, description="Used when no variable is set.")
    choices: list[str] | None = Field(default=None, description="The only values accepted, when the set is closed.")
    description: str = ""

    _check_env = field_validator("env")(_env_names)
    _check_one_line = field_validator("default", "description")(_one_line)


class VendorCredential(_Strict):
    """A secret a vendor's calls need. Never logged, and masked out of every error the vendor returns."""

    env: list[str] = Field(min_length=1, description="Variables read in order; the first that is set wins.")
    required: bool = Field(default=True, description="False when only some calls need it.")
    description: str = ""

    _check_env = field_validator("env")(_env_names)
    _check_one_line = field_validator("description")(_one_line)


class VendorOAuth2(_Strict):
    """How to mint an access token before a call. The token is cached until shortly before it expires."""

    grant: Literal["refresh_token", "client_credentials"]
    token_url: str = Field(description="Literal: sent as written, so it may hold no {placeholder}.")
    client_id: str = Field(description="A template, usually '{client_id}'.")
    client_secret: str = Field(description="A template, usually '{client_secret}'.")
    refresh_token: str | None = Field(default=None, description="A template; required for the refresh_token grant.")
    scope: str | None = Field(default=None, description="Literal: sent as written, so it may hold no {placeholder}.")
    client_auth: Literal["body", "basic"] = Field(
        default="body", description="Where the client id and secret go: the form body, or HTTP Basic."
    )
    client_id_param: str = Field(
        default="client_id",
        description="The form field the client id goes in, for vendors that rename it (TikTok: client_key).",
    )

    @model_validator(mode="after")
    def _grant_inputs(self) -> VendorOAuth2:
        if self.grant == "refresh_token" and not self.refresh_token:
            raise ValueError("the refresh_token grant needs a refresh_token template")
        if not self.token_url.startswith("https://"):
            raise ValueError(MSG_VENDOR_CATALOG_SCHEME.format(url=self.token_url))
        for name, value in (("token_url", self.token_url), ("scope", self.scope or "")):
            if "{" in value or "}" in value:
                raise ValueError(MSG_VENDOR_OAUTH_LITERAL.format(field=name, value=value))
        return self

    def templates(self) -> list[str]:
        return [t for t in (self.client_id, self.client_secret, self.refresh_token) if t]


class VendorInjection(_Strict):
    """One value placed on every request: a header, a query parameter or a top-level JSON body field.

    A ``body`` injection makes the request carry a JSON object even when the
    caller sent none, and overwrites a caller field of the same name.
    """

    location: Literal["header", "query", "body"] = Field(alias="in")
    name: str = Field(min_length=1)
    value: str = Field(description="A template, e.g. 'Bearer {access_token}' or '{api_key}'.")
    optional: bool = Field(
        default=False, description="Skip this one, rather than fail, when a value it names is not set."
    )


class VendorBasicAuth(_Strict):
    """HTTP Basic auth, rendered from templates."""

    username: str
    password: str = ""


class VendorAuth(_Strict):
    """Everything a vendor's calls carry to authenticate."""

    credentials: dict[str, VendorCredential] = Field(default_factory=dict)
    oauth2: VendorOAuth2 | None = None
    basic: VendorBasicAuth | None = None
    inject: list[VendorInjection] = Field(default_factory=list)

    def templates(self) -> list[str]:
        found = [injection.value for injection in self.inject]
        if self.basic is not None:
            found += [self.basic.username, self.basic.password]
        if self.oauth2 is not None:
            found += self.oauth2.templates()
        return found


class VendorParam(_Strict):
    """One input an endpoint takes, and where on the request it goes."""

    location: ParamLocation = Field(alias="in")
    type: ParamType = "string"
    required: bool = False
    description: str = ""
    example: Any = None
    enum: list[Any] | None = None
    wire_name: str | None = Field(
        default=None,
        description="The name the vendor uses, when it is not a valid Python identifier (e.g. 'filter[query]').",
    )


class VendorVerification(_Strict):
    """How an endpoint's entry was checked. ``live`` beats ``docs``."""

    docs: str | None = Field(default=None, description="The vendor documentation page the entry was written from.")
    live: str | None = Field(default=None, description="ISO date a live call matched the entry.")

    @model_validator(mode="after")
    def _some(self) -> VendorVerification:
        if not (self.docs or self.live):
            raise ValueError("verified needs 'docs' (a URL) or 'live' (a date)")
        return self


class VendorEndpoint(_Strict):
    """One named call on a vendor's API."""

    id: str = Field(pattern=VENDOR_ENDPOINT_ID_PATTERN)
    name: str = Field(min_length=1, description="What the call does, as a short action phrase.")
    summary: str = ""
    kind: EndpointKind = Field(description="'write' when the call changes anything on the vendor's side.")
    method: HttpMethod
    path: str = Field(description="Relative to base_url, with {placeholders} for path parameters.")
    params: dict[str, VendorParam] = Field(default_factory=dict)
    body_type: Literal["json", "form"] = "json"
    body_array: bool = Field(
        default=False, description="Send the body fields as a one-item JSON array, for APIs that take a list of tasks."
    )
    notes: list[str] = Field(default_factory=list, description="What a caller gets wrong without being told.")
    job: str | None = Field(
        default=None,
        description="The vendor-neutral job this call does ('ads.campaigns.performance'), for finding peers.",
    )
    host: str | None = Field(
        default=None, description="One of the vendor's named `hosts`, when this call is not on base_url."
    )
    credentials: list[str] | None = Field(
        default=None,
        description="The credentials this call needs, when not every one the vendor requires (an API-key check).",
    )
    docs_url: str
    verified: VendorVerification
    example_response: Any = None

    @model_validator(mode="after")
    def _consistent(self) -> VendorEndpoint:
        in_path = placeholders(self.path)
        declared = {self._wire(name, param) for name, param in self.params.items() if param.location == "path"}
        for name in in_path - declared:
            raise ValueError(MSG_VENDOR_CATALOG_PATH_PARAM.format(endpoint=self.id, name=name))
        for name in declared - in_path:
            raise ValueError(MSG_VENDOR_CATALOG_PATH_UNUSED.format(endpoint=self.id, name=name))
        has_body = any(param.location == "body" for param in self.params.values())
        if has_body and self.method == "GET":
            raise ValueError(MSG_VENDOR_CATALOG_BODY_ON_GET.format(endpoint=self.id, method=self.method))
        if not self.path.startswith("/"):
            raise ValueError(f"endpoint {self.id!r}: path must start with '/'")
        return self

    @staticmethod
    def _wire(name: str, param: VendorParam) -> str:
        return param.wire_name or name

    @property
    def vendor(self) -> str:
        return self.id.split(".", 1)[0]

    @property
    def writes(self) -> bool:
        return self.kind == "write"


class Vendor(_Strict):
    """One vendor's catalog file."""

    vendor: str = Field(pattern=VENDOR_ID_PATTERN)
    name: str
    category: str
    summary: str
    docs_url: str
    pricing_url: str | None = None
    base_url: str
    hosts: dict[str, str] = Field(
        default_factory=dict,
        description="Other API bases of the vendor, by name, for endpoints elsewhere; base_url's rules apply.",
    )
    settings: dict[str, VendorSetting] = Field(default_factory=dict)
    auth: VendorAuth = Field(default_factory=VendorAuth)
    headers: dict[str, str] = Field(
        default_factory=dict, description="Sent on every call: an API-version pin, say. Templates allowed."
    )
    notes: list[str] = Field(default_factory=list)
    sources: list[str] = Field(min_length=1, description="The vendor pages this file was written from.")
    curated: str = Field(description="ISO date the file was last checked against the vendor's docs.")
    endpoints: list[VendorEndpoint] = Field(default_factory=list)

    _check_one_line = field_validator("name", "summary", "docs_url", "pricing_url")(_one_line)

    @field_validator("category")
    @classmethod
    def _category(cls, value: str) -> str:
        if value not in VENDOR_CATEGORIES:
            raise ValueError(f"category {value!r} is not one of {', '.join(VENDOR_CATEGORIES)}")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Vendor:
        self._check_hosts()
        self._check_references()
        self._check_endpoints()
        return self

    def _check_hosts(self) -> None:
        for base in (self.base_url, *self.hosts.values()):
            if not base.startswith("https://"):
                raise ValueError(MSG_VENDOR_CATALOG_SCHEME.format(url=base))
            for name in placeholders(base):
                if name not in self.settings:
                    raise ValueError(MSG_VENDOR_CATALOG_BASE_URL_REF.format(name=name))
                # A free-form host would let a misconfigured variable carry the credential anywhere.
                if not self.settings[name].choices:
                    raise ValueError(MSG_VENDOR_CATALOG_SETTING_CHOICES.format(name=name))

    def _check_references(self) -> None:
        known = set(self.auth.credentials) | set(self.settings)
        if self.auth.oauth2 is not None:
            known.add(ACCESS_TOKEN)
        for template in [*self.auth.templates(), *self.headers.values()]:
            for name in placeholders(template):
                if name == ACCESS_TOKEN and self.auth.oauth2 is None:
                    raise ValueError(MSG_VENDOR_CATALOG_ACCESS_TOKEN)
                if name not in known:
                    raise ValueError(MSG_VENDOR_CATALOG_UNKNOWN_REF.format(name=name))

    def _check_endpoints(self) -> None:
        seen: set[str] = set()
        prefix = f"{self.vendor}."
        for endpoint in self.endpoints:
            if not endpoint.id.startswith(prefix):
                raise ValueError(MSG_VENDOR_CATALOG_ENDPOINT_PREFIX.format(endpoint=endpoint.id, prefix=prefix))
            unknown = set(endpoint.credentials or ()) - set(self.auth.credentials)
            if unknown:
                raise ValueError(f"endpoint {endpoint.id!r}: credentials {sorted(unknown)} are not the vendor's")
            if endpoint.host is not None and endpoint.host not in self.hosts:
                raise ValueError(f"endpoint {endpoint.id!r}: host {endpoint.host!r} is not one of the vendor's hosts")
            if endpoint.id in seen:
                raise ValueError(MSG_VENDOR_CATALOG_DUPLICATE_ENDPOINT.format(endpoint=endpoint.id))
            seen.add(endpoint.id)

    def base_for(self, host: str | None) -> str:
        """The API base a call goes to: ``base_url``, or the named host."""
        return self.base_url if host is None else self.hosts[host]

    def endpoint(self, endpoint_id: str) -> VendorEndpoint | None:
        return next((endpoint for endpoint in self.endpoints if endpoint.id == endpoint_id), None)

    def credential_variables(self) -> set[str]:
        """Every environment variable this vendor reads, credentials and settings alike."""
        names = {var for cred in self.auth.credentials.values() for var in cred.env}
        return names | {var for setting in self.settings.values() for var in setting.env}
