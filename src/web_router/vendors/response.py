"""Shaping one vendor call's exchange: the request's keyword arguments, and the answer or failure it got.

Apart from :mod:`web_router.vendors.caller` so the caller keeps only the
sending. Every secret the call carried is masked out of what comes back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from web_router.constants_vendors import (
    MSG_VENDOR_HTTP_ERROR,
    VENDOR_CONTENT_TYPE_HEADER,
    VENDOR_DROPPED_RESPONSE_HEADERS,
    VENDOR_ERROR_BODY_LIMIT,
    VENDOR_MASK,
)
from web_router.exceptions import ProviderError
from web_router.vendors.masking import mask, mask_answer
from web_router.vendors.plan import _Plan
from web_router.vendors.types import VendorResponse


@dataclass
class Prepared:
    """A request ready to send, and the secrets to mask out of anything it returns."""

    plan: _Plan
    url: str
    query: list[tuple[str, str]]
    headers: dict[str, str]
    auth: tuple[str, str] | None
    secrets: list[str]
    masked_query: set[str]
    body: Any = None


def send_kwargs(prepared: Prepared) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"params": prepared.query, "headers": prepared.headers}
    if prepared.auth is not None:
        kwargs["auth"] = prepared.auth
    body = prepared.body
    if body is not None:
        if prepared.plan.body_type == "form":
            kwargs["data"] = body
        else:
            kwargs["json"] = body
    return kwargs


def response(prepared: Prepared, answer: httpx.Response) -> VendorResponse:
    plan = prepared.plan
    shown = httpx.URL(prepared.url).copy_with(
        params=[(k, VENDOR_MASK if k in prepared.masked_query else v) for k, v in prepared.query]
    )
    if answer.status_code >= 400:
        body = mask(answer.text, prepared.secrets)[:VENDOR_ERROR_BODY_LIMIT]
        detail = MSG_VENDOR_HTTP_ERROR.format(
            status=answer.status_code, method=plan.method, path=plan.path, body=body or "(empty body)"
        )
        raise ProviderError(plan.vendor.vendor, detail)
    data: Any = None
    text: str | None = None
    if answer.content:
        try:
            data = mask_answer(answer.json(), prepared.secrets)
        except ValueError:
            text = mask_answer(answer.text, prepared.secrets)
    headers = {
        key: mask_answer(value, prepared.secrets)
        for key, value in answer.headers.items()
        if key.lower() not in VENDOR_DROPPED_RESPONSE_HEADERS
    }
    # From the masked headers, so a secret the vendor echoed into the content type stays masked there too.
    content_type = next((value for key, value in headers.items() if key.lower() == VENDOR_CONTENT_TYPE_HEADER), None)
    return VendorResponse(
        vendor=plan.vendor.vendor,
        endpoint=plan.endpoint,
        method=plan.method,
        url=str(shown),
        status_code=answer.status_code,
        data=data,
        text=text,
        content_type=content_type,
        headers=headers,
    )


def transport_error(prepared: Prepared, exc: httpx.HTTPError) -> ProviderError:
    message = mask(f"{type(exc).__name__}: {exc}", prepared.secrets)
    return ProviderError(prepared.plan.vendor.vendor, message)
