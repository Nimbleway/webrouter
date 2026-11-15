"""Brave's refused key, read from its body: Brave answers a bad subscription token with a 422, not a 401 or 403.

By status alone that is ``bad_request``, so ``auto`` moved on without the
refused-key warning. Search and answer both mix this in ahead of their HTTP
base, so the code in the body decides the kind.
"""

from __future__ import annotations

from typing import ClassVar

import httpx

from web_router.constants import HTTP_ERROR_BODY_LIMIT, MSG_HTTP_ERROR
from web_router.constants_brave import BRAVE_TOKEN_INVALID_CODE
from web_router.exceptions import ProviderError
from web_router.types_errors import ERROR_KIND_AUTH


class BraveTokenErrorsMixin:
    """Raises Brave's refused subscription token as an ``auth`` failure, whatever status it came with."""

    name: ClassVar[str]

    def _raise_for_status(self, response: httpx.Response) -> None:
        """An ``auth`` :class:`ProviderError` for a refused token; any other error as the HTTP base raises it."""
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if BRAVE_TOKEN_INVALID_CODE in exc.response.text:
                body = exc.response.text[:HTTP_ERROR_BODY_LIMIT]
                detail = MSG_HTTP_ERROR.format(status=exc.response.status_code, body=body)
                raise ProviderError(self.name, detail, original=exc, kind=ERROR_KIND_AUTH) from exc
        super()._raise_for_status(response)  # type: ignore[misc]
