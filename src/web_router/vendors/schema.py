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

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

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
    VENDOR_CATEGORIES,
    VENDOR_ENDPOINT_ID_PATTERN,
    VENDOR_ID_PATTERN,
)
from web_router.vendors.schema_auth import (
    VendorAuth,
    VendorBasicAuth,
    VendorCredential,
    VendorInjection,
    VendorOAuth2,
    VendorSetting,
)
from web_router.vendors.schema_base import (
    ACCESS_TOKEN,
    EndpointKind,
    HttpMethod,
    ParamLocation,
    ParamType,
    _one_line,
    _Strict,
    placeholders,
)

__all__ = [
    "ACCESS_TOKEN",
    "EndpointKind",
    "HttpMethod",
    "ParamLocation",
    "ParamType",
    "Vendor",
    "VendorAuth",
    "VendorBasicAuth",
    "VendorCredential",
    "VendorEndpoint",
    "VendorInjection",
    "VendorOAuth2",
    "VendorParam",
    "VendorSetting",
    "VendorVerification",
    "placeholders",
]


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
