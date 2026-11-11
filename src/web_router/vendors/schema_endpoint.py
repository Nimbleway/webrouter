"""The vendor catalog's endpoint models: one named call, its inputs, and how its entry was checked.

Apart from :mod:`web_router.vendors.schema`, which re-exports every model
here, so the file format's top-level model stays small.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from web_router.constants_vendors import (
    MSG_VENDOR_CATALOG_BODY_ON_GET,
    MSG_VENDOR_CATALOG_PATH_PARAM,
    MSG_VENDOR_CATALOG_PATH_UNUSED,
    VENDOR_ENDPOINT_ID_PATTERN,
)
from web_router.vendors.schema_base import EndpointKind, HttpMethod, ParamLocation, ParamType, _Strict, placeholders


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
