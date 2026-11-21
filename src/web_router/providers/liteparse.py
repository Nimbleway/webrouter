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

from web_router.constants import PROVIDER_LITEPARSE
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
    MSG_LITEPARSE_PRIVATE_HOP,
    MSG_LITEPARSE_REDIRECTS,
    MSG_LITEPARSE_TOO_LARGE,
)
from web_router.exceptions import ProviderError
from web_router.providers._deadline import job_budget
from web_router.providers._http_base import _HttpProvider
from web_router.providers._utils import compact
from web_router.providers.base import ExtractProvider
from web_router.routing.url_policy import any_web_url, public_only
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
        url, hop = urls[0], urls[0]
        for _ in range(LITEPARSE_MAX_REDIRECTS + 1):
            with self._client.stream("GET", hop, headers=self._headers_for(url, hop), **self._clamped()) as response:
                if (following := self._next_hop(url, response)) is None:
                    if response.is_error:
                        response.read()
                    self._raise_for_status(response)
                    data = _capped(url, response, response.iter_bytes())
                    return self._to_response(request, url, self._parse(request, url, data), response)
            hop = following
        raise ProviderError(self.name, MSG_LITEPARSE_REDIRECTS.format(url=url, limit=LITEPARSE_MAX_REDIRECTS))

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        url, hop = urls[0], urls[0]
        for _ in range(LITEPARSE_MAX_REDIRECTS + 1):
            headers = self._headers_for(url, hop)
            async with self._async_client.stream("GET", hop, headers=headers, **self._clamped()) as response:
                if (following := self._next_hop(url, response)) is None:
                    if response.is_error:
                        await response.aread()
                    self._raise_for_status(response)
                    chunks = [chunk async for chunk in _limited(url, response, response.aiter_bytes())]
                    result = await self._aparse(request, url, b"".join(chunks))
                    return self._to_response(request, url, result, response)
            hop = following
        raise ProviderError(self.name, MSG_LITEPARSE_REDIRECTS.format(url=url, limit=LITEPARSE_MAX_REDIRECTS))

    def _headers_for(self, url: str, hop: str) -> dict[str, str]:
        """The caller's headers while the fetch stays on ``url``'s own host; none once a redirect leaves it."""
        return self._own_headers if httpx.URL(hop).netloc == httpx.URL(url).netloc else {}

    def _next_hop(self, url: str, response: httpx.Response) -> str | None:
        """Where a redirect points, or ``None`` when ``response`` is the file; raises for a hop it may not take.

        A public URL may not redirect to a private host: a caller handing in a
        public link never meant this machine to fetch its own network.
        """
        if not (self._follow and response.is_redirect):
            return None
        target = str(response.url.join(response.headers["location"]))
        why = public_only(target) if public_only(url) is None else any_web_url(target)
        if why is not None:
            raise ProviderError(self.name, MSG_LITEPARSE_PRIVATE_HOP.format(url=url, why=why), kind=ERROR_KIND_TARGET)
        return target

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


def _check_length(url: str, response: httpx.Response) -> None:
    """Refuse up front a file whose stated length is already too large."""
    length = response.headers.get("content-length", "")
    if length.isdigit() and int(length) > LITEPARSE_MAX_BYTES:
        raise _too_large(url)


def _capped(url: str, response: httpx.Response, chunks: Iterator[bytes]) -> bytes:
    """The body, refused once it passes :data:`LITEPARSE_MAX_BYTES`, before it fills memory."""
    _check_length(url, response)
    data = bytearray()
    for chunk in chunks:
        data += chunk
        if len(data) > LITEPARSE_MAX_BYTES:
            raise _too_large(url)
    return bytes(data)


async def _limited(url: str, response: httpx.Response, chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """``chunks``, refused once they pass :data:`LITEPARSE_MAX_BYTES`; the async side of :func:`_capped`."""
    _check_length(url, response)
    total = 0
    async for chunk in chunks:
        total += len(chunk)
        if total > LITEPARSE_MAX_BYTES:
            raise _too_large(url)
        yield chunk


def _too_large(url: str) -> ProviderError:
    return ProviderError(PROVIDER_LITEPARSE, MSG_LITEPARSE_TOO_LARGE.format(url=url, limit=LITEPARSE_MAX_BYTES))
