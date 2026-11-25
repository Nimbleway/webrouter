"""LlamaParse provider: a document parsed by LlamaCloud's Parse API.

The opt-in counterpart of LiteParse for extract ``auto``'s document step
(``provider_config={"auto": {"document_parser": "llamaparse"}}``), or a backend
of its own. A parse is a job: it is created from the URL, polled until it ends,
then read in the format asked for. Reads ``LLAMA_CLOUD_API_KEY``. Install with
the ``extract`` extra -- it needs ``httpx`` alone.

:meth:`parse_bytes` uploads a file instead, for one fetched some other way.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import PurePosixPath
from typing import Any, ClassVar
from urllib.parse import urlsplit

from web_router.constants import ENV_LLAMA_CLOUD_API_KEY, PROVIDER_LLAMAPARSE
from web_router.constants_llamaparse import (
    LLAMAPARSE_API_KEY_PREFIX,
    LLAMAPARSE_BASE_URL,
    LLAMAPARSE_DONE,
    LLAMAPARSE_ENDED,
    LLAMAPARSE_EXTRACT_DEFAULT_FORMAT,
    LLAMAPARSE_EXTRACT_FORMATS,
    LLAMAPARSE_JOB_PATH,
    LLAMAPARSE_PARSE_PATH,
    LLAMAPARSE_POLL_SECONDS,
    LLAMAPARSE_TIER,
    LLAMAPARSE_UPLOAD_PATH,
    LLAMAPARSE_VERSION,
    LLAMAPARSE_WAIT_SECONDS,
    MSG_LLAMAPARSE_FAILED,
    MSG_LLAMAPARSE_JOB_ERROR,
    MSG_LLAMAPARSE_NO_JOB,
    MSG_LLAMAPARSE_STILL_RUNNING,
)
from web_router.exceptions import ProviderError
from web_router.providers._http_base import _HttpProvider
from web_router.providers._job_wait import wait_until
from web_router.providers._utils import build_payload
from web_router.providers.base import ExtractProvider
from web_router.types import EXTRACT_FORMAT_ONLY, ExtractedDocument, ExtractFormat, ExtractRequest, ExtractResponse
from web_router.types_errors import ERROR_KIND_TIMEOUT


class LlamaParseExtractProvider(_HttpProvider, ExtractProvider):
    """Parses a document through LlamaParse, waiting on the job."""

    name: ClassVar[str] = PROVIDER_LLAMAPARSE
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_FORMAT_ONLY
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(LLAMAPARSE_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = LLAMAPARSE_EXTRACT_DEFAULT_FORMAT
    base_url: ClassVar[str] = LLAMAPARSE_BASE_URL
    env_api_key: ClassVar[str | None] = ENV_LLAMA_CLOUD_API_KEY
    api_key_prefix: ClassVar[str] = LLAMAPARSE_API_KEY_PREFIX

    def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        body = self._job_fields(request, source_url=urls[0])
        created = self._payload(self._client.post(LLAMAPARSE_PARSE_PATH, json=body, **self._clamped()))
        return self._waited(request, urls[0], _job_id(created))

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        body = self._job_fields(request, source_url=urls[0])
        response = await self._async_client.post(LLAMAPARSE_PARSE_PATH, json=body, **self._clamped())
        return await self._awaited(request, urls[0], _job_id(self._payload(response)))

    def parse_bytes(self, data: bytes, *, url: str, format: ExtractFormat | None = None) -> ExtractResponse:
        """Parse a file already in hand, uploaded rather than fetched by LlamaCloud; ``url`` labels the document."""
        request = ExtractRequest(urls=[url], format=format)
        self.validate(request)
        try:
            response = self._client.post(LLAMAPARSE_UPLOAD_PATH, **self._upload(request, url, data), **self._clamped())
            return self._waited(request, url, _job_id(self._payload(response)))
        except ProviderError:
            raise
        except Exception as exc:
            raise self._failure(exc) from exc

    def _job_fields(self, request: ExtractRequest, **fixed: Any) -> dict[str, Any]:
        return build_payload(request, tier=LLAMAPARSE_TIER, version=LLAMAPARSE_VERSION, **fixed)

    def _upload(self, request: ExtractRequest, url: str, data: bytes) -> dict[str, Any]:
        # The path's last segment only: a signed URL's query holds its token, and LlamaCloud needs no URL here.
        name = PurePosixPath(urlsplit(url).path).name or "document"
        return {"files": {"file": (name, data)}, "data": {"configuration": json.dumps(self._job_fields(request))}}

    def _waited(self, request: ExtractRequest, url: str, job_id: str) -> ExtractResponse:
        try:
            return self._waiting(request, url, job_id)
        except Exception as exc:
            raise _naming(job_id, exc) from exc

    async def _awaited(self, request: ExtractRequest, url: str, job_id: str) -> ExtractResponse:
        try:
            return await self._awaiting(request, url, job_id)
        except Exception as exc:
            raise _naming(job_id, exc) from exc

    def _waiting(self, request: ExtractRequest, url: str, job_id: str) -> ExtractResponse:
        until, path = wait_until(LLAMAPARSE_WAIT_SECONDS), LLAMAPARSE_JOB_PATH.format(job_id=job_id)
        while (status := _status(self._payload(self._client.get(path, **self._clamped())))) not in LLAMAPARSE_ENDED:
            if time.monotonic() >= until:
                raise _still_running(self.name, job_id, status)
            time.sleep(max(0.0, min(LLAMAPARSE_POLL_SECONDS, until - time.monotonic())))
        params = {"expand": LLAMAPARSE_EXTRACT_FORMATS[self.format_of(request)]}
        return self._ended(
            request, url, job_id, self._payload(self._client.get(path, params=params, **self._clamped()))
        )

    async def _awaiting(self, request: ExtractRequest, url: str, job_id: str) -> ExtractResponse:
        until, path = wait_until(LLAMAPARSE_WAIT_SECONDS), LLAMAPARSE_JOB_PATH.format(job_id=job_id)
        while (status := _status(self._payload(await self._async_client.get(path, **self._clamped())))) not in (
            LLAMAPARSE_ENDED
        ):
            if time.monotonic() >= until:
                raise _still_running(self.name, job_id, status)
            await asyncio.sleep(max(0.0, min(LLAMAPARSE_POLL_SECONDS, until - time.monotonic())))
        params = {"expand": LLAMAPARSE_EXTRACT_FORMATS[self.format_of(request)]}
        response = await self._async_client.get(path, params=params, **self._clamped())
        return self._ended(request, url, job_id, self._payload(response))

    def _ended(self, request: ExtractRequest, url: str, job_id: str, payload: dict[str, Any]) -> ExtractResponse:
        job = payload.get("job") or {}
        if job.get("status") != LLAMAPARSE_DONE:
            detail = MSG_LLAMAPARSE_FAILED.format(
                job_id=job_id, status=job.get("status"), error=job.get("error_message")
            )
            raise ProviderError(self.name, detail)
        body_format = self.format_of(request)
        document = ExtractedDocument(
            url=url,
            content=payload.get(LLAMAPARSE_EXTRACT_FORMATS[body_format]) or "",
            format=body_format,
            extra={"job_id": job_id, **({"usage": job["usage"]} if job.get("usage") else {})},
        )
        return ExtractResponse(provider=self.name, documents=[document], request_id=job_id, raw=[payload])


def _job_id(payload: dict[str, Any]) -> str:
    job_id = payload.get("id") or (payload.get("job") or {}).get("id")
    if not job_id:
        raise ProviderError(PROVIDER_LLAMAPARSE, MSG_LLAMAPARSE_NO_JOB.format(fields=sorted(payload)))
    return str(job_id)


def _status(payload: dict[str, Any]) -> str | None:
    return (payload.get("job") or {}).get("status")


def _still_running(provider: str, job_id: str, status: str | None) -> ProviderError:
    return ProviderError(
        provider, MSG_LLAMAPARSE_STILL_RUNNING.format(job_id=job_id, status=status), kind=ERROR_KIND_TIMEOUT
    )


def _naming(job_id: str, exc: Exception) -> Exception:
    """``exc`` as an error naming the accepted, billed job so its id is never lost; one naming it already, as is."""
    if job_id in str(exc):
        return exc
    detail = MSG_LLAMAPARSE_JOB_ERROR.format(job_id=job_id, error=exc)
    return ProviderError(PROVIDER_LLAMAPARSE, detail, original=exc, kind=getattr(exc, "kind", None))
