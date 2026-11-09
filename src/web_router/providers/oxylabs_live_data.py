"""Oxylabs live data: its dedicated sources -- ``amazon_product``, ``google_search`` and the rest -- run with parsing.

A source is a scraper Oxylabs maintains for one kind of page, named as its
API names it, run through the same Realtime endpoint Oxylabs' extract uses
with ``parse: true``, so the answer is the page's fields rather than its HTML.
The inputs are the source's own: ``query`` (an ASIN, a search term) for some,
``url`` for others, plus its parameters such as ``domain`` and
``geo_location`` -- sent as given.

Written from https://developers.oxylabs.io (Realtime integration, dedicated
parsers, response codes), read on 2026-11-09. **Not yet verified live**: no
Oxylabs credentials were available.

Oxylabs has no endpoint that lists its sources, so the catalogue is the list
of sources with a dedicated parser that its docs publish, and a description
carries no input schema: the docs do not give every source's inputs. A
result's ``created_at`` is kept in ``extra`` rather than reported as
``fetched_at``, because the docs never say it is the fetch time or name its
timezone. A page that was not fetched, or not parsed, raises; a partial parse
(``parse_status_code`` 12004 or 12005) is a record, with its code in ``extra``.
"""

from __future__ import annotations

from base64 import b64encode
from typing import Any, ClassVar

from web_router.constants import ENV_OXYLABS_PASSWORD, ENV_OXYLABS_USERNAME, OXYLABS_BASE_URL, PROVIDER_OXYLABS
from web_router.constants_live_data_vendors import (
    MSG_OXYLABS_NOT_FETCHED,
    MSG_OXYLABS_NOT_PARSED,
    OXYLABS_DOCS_URL,
    OXYLABS_FAILED_FETCH_FLOOR,
    OXYLABS_LIVE_DATA_PATH,
    OXYLABS_PARSE_OK,
    OXYLABS_SOURCES,
)
from web_router.exceptions import LiveDataSourceNotFoundError, ProviderError
from web_router.providers._http_live_data import HttpLiveDataProvider
from web_router.providers._utils import resolve_setting
from web_router.types_live_data import (
    LIVE_DATA_PARAMETERS,
    LiveDataRequest,
    LiveDataResponse,
    LiveDataSource,
    LiveDataSourcePage,
)

_RESULT_MAPPED = frozenset({"content", "url", "status_code"})


class OxylabsLiveDataProvider(HttpLiveDataProvider):
    """Runs Oxylabs' dedicated sources, parsed, as live data sources."""

    name: ClassVar[str] = PROVIDER_OXYLABS
    supported_parameters: ClassVar[frozenset[str]] = LIVE_DATA_PARAMETERS

    run_path: ClassVar[str] = OXYLABS_LIVE_DATA_PATH
    base_url: ClassVar[str] = OXYLABS_BASE_URL
    env_api_key: ClassVar[str] = ENV_OXYLABS_PASSWORD

    def __init__(self, *, username: str | None = None, **kwargs: Any) -> None:
        """
        Args:
            username: Oxylabs API username. Falls back to ``OXYLABS_USERNAME``.
            **kwargs: ``api_key`` is the password and falls back to ``OXYLABS_PASSWORD``.
        """
        # Resolved before super(), which needs it to build the auth header.
        self._username = resolve_setting(username, env_var=ENV_OXYLABS_USERNAME, provider=self.name, setting="username")
        super().__init__(**kwargs)

    def _auth_headers(self, key: str) -> dict[str, str]:
        """Oxylabs takes HTTP Basic credentials, not a bearer token."""
        return {self.api_key_header: "Basic " + b64encode(f"{self._username}:{key}".encode()).decode()}

    # -- run ----------------------------------------------------------------

    def _run(self, request: LiveDataRequest) -> LiveDataResponse:
        return self._to_response(request, self._send("POST", self.run_path, json=self._build_payload(request)))

    async def _arun(self, request: LiveDataRequest) -> LiveDataResponse:
        return self._to_response(
            request, await self._asend("POST", OXYLABS_LIVE_DATA_PATH, json=self._build_payload(request))
        )

    @staticmethod
    def _build_payload(request: LiveDataRequest) -> dict[str, Any]:
        # The source's own inputs as given, parsing on; provider_options last, as on every backend.
        return {**dict(request.inputs), "source": request.source, "parse": True, **request.provider_options}

    def _to_response(self, request: LiveDataRequest, payload: dict[str, Any]) -> LiveDataResponse:
        records: list[dict[str, Any]] = []
        extras: list[dict[str, Any]] = []
        for result in (payload or {}).get("results") or []:
            self._check(request, result)
            records.append(result["content"])
            extras.append({key: value for key, value in result.items() if key not in _RESULT_MAPPED})
        first = ((payload or {}).get("results") or [{}])[0]
        return LiveDataResponse(
            provider=self.name,
            source=request.source,
            inputs=dict(request.inputs),
            records=records,
            url=first.get("url"),
            status_code=first.get("status_code"),
            extra={"results": extras},
            raw=payload,
        )

    def _check(self, request: LiveDataRequest, result: dict[str, Any]) -> None:
        """Raise for a result whose page was not fetched or not parsed; partial parses pass."""
        status, url, content = result.get("status_code"), result.get("url"), result.get("content")
        if isinstance(status, int) and status >= OXYLABS_FAILED_FETCH_FLOOR:
            raise ProviderError(
                self.name, MSG_OXYLABS_NOT_FETCHED.format(source=request.source, status=status, url=url)
            )
        code = content.get("parse_status_code") if isinstance(content, dict) else None
        if not isinstance(content, dict) or (code is not None and code not in OXYLABS_PARSE_OK):
            raise ProviderError(self.name, MSG_OXYLABS_NOT_PARSED.format(source=request.source, code=code, url=url))

    # -- catalogue ----------------------------------------------------------

    def _list(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        start = offset or 0
        names = OXYLABS_SOURCES[start:] if limit is None else OXYLABS_SOURCES[start : start + limit]
        return LiveDataSourcePage(
            provider=self.name,
            sources=[self._source(name) for name in names],
            total=len(OXYLABS_SOURCES),
            offset=start,
            limit=limit,
        )

    async def _alist(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._list(limit=limit, offset=offset)

    def _get(self, name: str) -> LiveDataSource:
        if name not in OXYLABS_SOURCES:
            raise LiveDataSourceNotFoundError(self.name, name)
        return self._source(name)

    async def _aget(self, name: str) -> LiveDataSource:
        return self._get(name)

    def _source(self, name: str) -> LiveDataSource:
        return LiveDataSource(
            provider=self.name,
            name=name,
            description=f"Oxylabs' dedicated {name} parser; inputs (query or url, and parameters): {OXYLABS_DOCS_URL}",
            domain=name.split("_", 1)[0],
            published=True,
        )
