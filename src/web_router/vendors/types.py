"""What the vendor methods return."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VendorSummary(BaseModel):
    """One vendor, as :meth:`~web_router.WebRouter.vendors` lists it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    vendor: str
    name: str
    category: str
    summary: str
    docs_url: str
    endpoint_count: int
    configured: bool = Field(description="Every credential its calls require is set.")
    missing: list[str] = Field(
        default_factory=list, description="The variable to set for each required credential that is not, first choice."
    )


class VendorResponse(BaseModel):
    """A vendor's answer to one call, untouched.

    Nothing here is normalized: :attr:`data` is the vendor's own JSON, in the
    vendor's own field names and units (Google Ads money in micros, Datadog
    timestamps in epoch milliseconds), because there is no second vendor whose
    shape it would be made to match. An HTTP error raises
    :class:`~web_router.ProviderError` instead of coming back here, with every
    credential masked out of the vendor's message.
    """

    model_config = ConfigDict(extra="forbid", ser_json_bytes="base64", val_json_bytes="base64")

    vendor: str
    endpoint: str | None = Field(default=None, description="The catalog endpoint called; None for a raw request.")
    method: str
    url: str = Field(description="The URL called, with any credential sent in the query masked.")
    status_code: int
    data: Any = Field(default=None, description="The decoded JSON body; None when the body was not JSON.")
    text: str | None = Field(default=None, description="The body as text, when it was not JSON.")
    content: bytes | None = Field(
        default=None,
        description="The body's bytes when its content type is not text (an image, a PDF); URL-safe base64 in JSON.",
    )
    content_type: str | None = None
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="The response headers, minus Set-Cookie: rate-limit and paging hints live here.",
    )
