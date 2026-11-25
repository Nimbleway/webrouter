"""LiteParse provider: a document's text, parsed on this machine.

Downloads the file the URL points at and parses it in process with
`LiteParse <https://github.com/run-llama/liteparse>`_, so it needs no key and
sends the file nowhere. Install with the ``extract`` extra.

PDFs parse on their own. Word, PowerPoint and Excel files are converted with
LibreOffice first, and fail without it. A URL that serves a web page rather
than a file fails too: this reads documents, and extract ``auto`` sends it
only the URLs that name one (:mod:`web_router.providers._auto_documents`).
:meth:`parse_bytes` parses a file already in hand.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from typing import Any, ClassVar

import httpx
from liteparse import LiteParse, ParseResult, ParseTimeoutError

from web_router.constants import HTTP_ERROR_BODY_LIMIT, PROVIDER_LITEPARSE
from web_router.constants_liteparse import (
    LITEPARSE_ACCEPT,
    LITEPARSE_EXTRACT_DEFAULT_FORMAT,
    LITEPARSE_EXTRACT_FORMATS,
    LITEPARSE_GONE_STATUSES,
    LITEPARSE_MAX_BYTES,
    LITEPARSE_MAX_REDIRECTS,
    LITEPARSE_MIN_PARSE_SECONDS,
    LITEPARSE_PARSE_TIMEOUT,
    MSG_LITEPARSE_FAILED,
    MSG_LITEPARSE_REDIRECTS,
    MSG_LITEPARSE_TOO_LARGE,
)
from web_router.exceptions import ProviderError
from web_router.providers._deadline import job_budget
from web_router.providers._http_base import _HttpProvider
from web_router.providers._liteparse_hops import next_hop, pinned
from web_router.providers._utils import compact
from web_router.providers.base import ExtractProvider
from web_router.types import EXTRACT_FORMAT_ONLY, ExtractedDocument, ExtractFormat, ExtractRequest, ExtractResponse
from web_router.types_errors import ERROR_KIND_TARGET, ERROR_KIND_TIMEOUT


class LiteParseExtractProvider(_HttpProvider, ExtractProvider):
    """Fetches a document and parses it locally with LiteParse."""

    name: ClassVar[str] = PROVIDER_LITEPARSE
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_FORMAT_ONLY
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(LITEPARSE_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = LITEPARSE_EXTRACT_DEFAULT_FORMAT
    base_url: ClassVar[str] = ""

    def __init__(self, **kwargs: Any) -> None:
        """
        Args:
            **kwargs: As :class:`~web_router.providers.HttpExtractProvider`.
                Redirects are followed unless ``follow_redirects=False``, since
                file links often redirect to storage. Caller ``headers`` go to
                the URL's own host only, and a public URL may not redirect to a
                private one.
        """
        self._own_headers: dict[str, str] = dict(kwargs.pop("headers", None) or {})
        self._follow = bool(kwargs.pop("follow_redirects", True))
        super().__init__(headers={"Accept": LITEPARSE_ACCEPT}, follow_redirects=False, **kwargs)

    def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        response, data = self._fetch(urls[0])
        return self._to_response(request, urls[0], self._parse(request, urls[0], data), response)

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        response, data = await self._afetch(urls[0])
        return self._to_response(request, urls[0], await self._aparse(request, urls[0], data), response)

    def _fetch(self, url: str) -> tuple[httpx.Response, bytes]:
        """The response that serves ``url``'s file once its redirects are followed, and its body."""
        hop = url
        for _ in range(LITEPARSE_MAX_REDIRECTS + 1):
            target, headers, extensions = pinned(url, hop, self._own_headers)
            kwargs = {"headers": headers, "extensions": extensions, **self._clamped()}
            with self._client.stream("GET", target, **kwargs) as response:
                if (following := next_hop(url, hop, response, follow=self._follow)) is None:
                    return response, self._body(url, response, _read(response.iter_bytes(), _limit(url, response)))
            hop = following
        raise ProviderError(self.name, MSG_LITEPARSE_REDIRECTS.format(url=url, limit=LITEPARSE_MAX_REDIRECTS))

    async def _afetch(self, url: str) -> tuple[httpx.Response, bytes]:
        """Async counterpart of :meth:`_fetch`."""
        hop = url
        for _ in range(LITEPARSE_MAX_REDIRECTS + 1):
            target, headers, extensions = await asyncio.to_thread(pinned, url, hop, self._own_headers)
            kwargs = {"headers": headers, "extensions": extensions, **self._clamped()}
            async with self._async_client.stream("GET", target, **kwargs) as response:
                if (following := next_hop(url, hop, response, follow=self._follow)) is None:
                    data = await _aread(response.aiter_bytes(), _limit(url, response))
                    return response, self._body(url, response, data)
            hop = following
        raise ProviderError(self.name, MSG_LITEPARSE_REDIRECTS.format(url=url, limit=LITEPARSE_MAX_REDIRECTS))

    def _body(self, url: str, response: httpx.Response, data: bytes) -> bytes:
        """``data``, read up to :func:`_limit`, as the file; raises for an error status or a file past the cap."""
        if response.is_error:
            content_type = {key: value for key, value in response.headers.items() if key == "content-type"}
            head = data[:HTTP_ERROR_BODY_LIMIT]
            error = httpx.Response(response.status_code, headers=content_type, content=head, request=response.request)
            self._raise_for_status(error)
        if len(data) > LITEPARSE_MAX_BYTES:
            raise _too_large(url)
        return data

    def parse_bytes(self, data: bytes, *, url: str, format: ExtractFormat | None = None) -> ExtractResponse:
        """Parse a file already in hand, for one fetched some other way (a proxy, say); ``url`` labels the document."""
        request = ExtractRequest(urls=[url], format=format)
        self.validate(request)
        return self._to_response(request, url, self._parse(request, url, data))

    def _error_kind(self, exc: httpx.HTTPStatusError) -> str | None:
        """The URL fetched is the file itself, not an API, so a 404 is the target's and not worth another backend."""
        return ERROR_KIND_TARGET if exc.response.status_code in LITEPARSE_GONE_STATUSES else None

    def _parser(self, request: ExtractRequest) -> LiteParse:
        """A parser in its own worker process, killed once the call's time (or the default cap) runs out."""
        timeout = max(LITEPARSE_MIN_PARSE_SECONDS, job_budget(LITEPARSE_PARSE_TIMEOUT))
        output = LITEPARSE_EXTRACT_FORMATS[self.format_of(request)]
        return LiteParse(output_format=output, quiet=True, pool_size=1, parse_timeout=timeout)

    def _parse(self, request: ExtractRequest, url: str, data: bytes) -> ParseResult:
        return self._parsed(self._parser(request), url, data)

    async def _aparse(self, request: ExtractRequest, url: str, data: bytes) -> ParseResult:
        parser = self._parser(request)
        try:
            return await asyncio.to_thread(self._parsed, parser, url, data)
        except asyncio.CancelledError:
            # The thread cannot be stopped, but its worker process can: closing the parser ends the parse.
            threading.Thread(target=parser.close, daemon=True).start()
            raise

    def _parsed(self, parser: LiteParse, url: str, data: bytes) -> ParseResult:
        try:
            return parser.parse(data)
        except ParseTimeoutError as exc:
            detail = MSG_LITEPARSE_FAILED.format(url=url, error=exc)
            raise ProviderError(self.name, detail, original=exc, kind=ERROR_KIND_TIMEOUT) from exc
        except Exception as exc:
            raise ProviderError(self.name, MSG_LITEPARSE_FAILED.format(url=url, error=exc), original=exc) from exc
        finally:
            parser.close()

    def _to_response(
        self, request: ExtractRequest, url: str, result: ParseResult, response: httpx.Response | None = None
    ) -> ExtractResponse:
        content_type = response.headers.get("content-type") if response is not None else None
        document = ExtractedDocument(
            url=url,
            content=result.text,
            format=self.format_of(request),
            status_code=response.status_code if response is not None else None,
            extra=compact({"total_pages": result.total_pages, "content_type": content_type}),
        )
        return ExtractResponse(provider=self.name, documents=[document])


def _limit(url: str, response: httpx.Response) -> int:
    """How much of the body to read: an error's first bytes, or a file up to the cap, refused up front past it."""
    if response.is_error:
        return HTTP_ERROR_BODY_LIMIT
    length = response.headers.get("content-length", "")
    if length.isdigit() and int(length) > LITEPARSE_MAX_BYTES:
        raise _too_large(url)
    return LITEPARSE_MAX_BYTES


def _read(chunks: Iterator[bytes], limit: int) -> bytes:
    """``chunks`` joined, the rest left unread once past ``limit`` bytes."""
    data = bytearray()
    for chunk in chunks:
        data += chunk
        if len(data) > limit:
            break
    return bytes(data)


async def _aread(chunks: AsyncIterator[bytes], limit: int) -> bytes:
    """The async side of :func:`_read`."""
    data = bytearray()
    async for chunk in chunks:
        data += chunk
        if len(data) > limit:
            break
    return bytes(data)


def _too_large(url: str) -> ProviderError:
    return ProviderError(PROVIDER_LITEPARSE, MSG_LITEPARSE_TOO_LARGE.format(url=url, limit=LITEPARSE_MAX_BYTES))
