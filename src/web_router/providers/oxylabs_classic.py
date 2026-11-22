"""Oxylabs live data over the Web Scraper API: its parsed sources -- ``amazon_product``, ``google_search`` and the rest.

The Web Scraper API is the one Oxylabs' extract calls: ``POST /v1/queries`` on
``realtime.oxylabs.io``, authenticated with ``OXYLABS_USERNAME`` and
``OXYLABS_PASSWORD`` over HTTP Basic. Oxylabs' docs now call it "Classic",
beside the newer Web API (:mod:`~web_router.providers.oxylabs_live_data`);
:mod:`~web_router.providers.oxylabs_live_data_any` serves both under one name.

A source is a ``source`` value Oxylabs parses. The API lists none, so the
catalogue ships in ``data/oxylabs_classic_sources.json``, read from the
targets section of Oxylabs' docs by ``scripts/oxylabs_classic.py``: each
source's inputs, which are mandatory, and which travel under ``context``. A
run sends ``parse: true`` where Oxylabs documents a parser -- a source that
answers in JSON of its own refuses it -- and answers with each result's
``content`` as a record, read by :mod:`~web_router.providers.oxylabs_response`.
Verified live on 2026-11-17: ``google_search``, ``amazon_search``,
``amazon_product``, ``google_ai_mode``, ``youtube_subtitles``,
``youtube_video_trainability`` and ``google_trends_explore``, and
``lazada_search``'s ``parse_status_code`` 12003, a source with no parser.
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any, ClassVar

from web_router.constants import (
    ENV_OXYLABS_PASSWORD,
    ENV_OXYLABS_USERNAME,
    OXYLABS_BASE_URL,
    OXYLABS_EXTRACT_PATH,
    PROVIDER_OXYLABS,
)
from web_router.constants_oxylabs_classic import (
    OXYLABS_CLASSIC_API,
    OXYLABS_CLASSIC_CONTEXT,
    OXYLABS_CLASSIC_DATA_DIR,
    OXYLABS_CLASSIC_DATA_FILE,
    OXYLABS_CLASSIC_PARSE,
    OXYLABS_CLASSIC_SOURCE,
    OXYLABS_CLASSIC_SOURCE_DESCRIPTION,
    OXYLABS_CLASSIC_TRANSPORT_PARAMS,
)
from web_router.exceptions_live_data import LiveDataSourceNotFoundError
from web_router.providers._http_live_data import HttpLiveDataProvider
from web_router.providers._utils import resolve_setting
from web_router.providers.oxylabs import basic_auth
from web_router.providers.oxylabs_response import WEB_SCRAPER_API, to_response
from web_router.types_live_data import (
    LIVE_DATA_PARAMETERS,
    LiveDataRequest,
    LiveDataResponse,
    LiveDataSource,
    LiveDataSourcePage,
)


@cache
def classic_sources() -> dict[str, dict[str, Any]]:
    """Every parsed Web Scraper API source, by name, as ``data/oxylabs_classic_sources.json`` records it."""
    text = (resources.files("web_router") / OXYLABS_CLASSIC_DATA_DIR / OXYLABS_CLASSIC_DATA_FILE).read_text("utf-8")
    return {entry["name"]: entry for entry in json.loads(text)["sources"]}


def _input_schema(entry: dict[str, Any]) -> dict[str, Any]:
    """The source's inputs as JSON Schema: names, which are required, and the docs' defaults. No types: none are."""
    inputs: dict[str, dict[str, Any]] = entry["inputs"]
    properties = {name: {"default": spec["default"]} if spec["default"] else {} for name, spec in inputs.items()}
    required = [name for name, spec in inputs.items() if spec["required"]]
    return {"type": "object", "properties": properties, "required": required}


def listed(provider: str, entry: dict[str, Any]) -> LiveDataSource:
    """A Web Scraper API source as a live data source, its input schema included: the catalogue carries it."""
    name: str = entry["name"]
    _, _, kind = name.partition("_")
    return LiveDataSource(
        provider=provider,
        name=name,
        description=OXYLABS_CLASSIC_SOURCE_DESCRIPTION.format(name=name, docs=entry["docs"]),
        domain=entry["site"],
        entity_type=kind or None,
        input_schema=_input_schema(entry),
        published=True,
        extra={"api": OXYLABS_CLASSIC_API, "category": entry["category"], "context": entry["context"]},
    )


class OxylabsClassicLiveDataProvider(HttpLiveDataProvider):
    """Runs the Oxylabs Web Scraper API's parsed sources as live data sources."""

    name: ClassVar[str] = PROVIDER_OXYLABS
    supported_parameters: ClassVar[frozenset[str]] = LIVE_DATA_PARAMETERS

    base_url: ClassVar[str] = OXYLABS_BASE_URL
    run_path: ClassVar[str] = OXYLABS_EXTRACT_PATH
    env_api_key: ClassVar[str] = ENV_OXYLABS_PASSWORD

    def __init__(self, *, username: str | None = None, **kwargs: Any) -> None:
        """
        Args:
            username: Oxylabs API username. Falls back to ``OXYLABS_USERNAME``.
            **kwargs: As :class:`~web_router.providers._http_live_data.HttpLiveDataProvider`;
                ``api_key`` is the password and falls back to ``OXYLABS_PASSWORD``. The Web API's
                ``web_api_key`` is not one: :class:`~web_router.providers.oxylabs_live_data_any.OxylabsLiveDataProvider`
                passes each API only its own credentials.
        """
        # Resolved before super(), which needs it to build the auth header.
        self._username = resolve_setting(username, env_var=ENV_OXYLABS_USERNAME, provider=self.name, setting="username")
        super().__init__(**kwargs)

    def _auth_headers(self, key: str) -> dict[str, str]:
        return {self.api_key_header: basic_auth(self._username, key)}

    @staticmethod
    def knows(source: str) -> bool:
        """Whether ``source`` is one of this API's parsed sources."""
        return source.strip() in classic_sources()

    def _entry(self, source: str) -> dict[str, Any]:
        entry = classic_sources().get(source.strip())
        if entry is None:
            raise LiveDataSourceNotFoundError(self.name, source)
        return entry

    def _build_payload(self, request: LiveDataRequest) -> dict[str, Any]:
        """The source, the inputs -- a ``context`` one in the array -- its fixed values, and ``parse: true`` last.

        A fixed value (``google_ai_mode``'s ``render: "html"``) is the only one the source takes, so a caller's
        own value for it does not replace it.

        A source Oxylabs answers in JSON of its own refuses ``parse``, so it is sent only where the docs list it.
        """
        entry = self._entry(request.source)
        given = {**request.inputs, **request.provider_options}
        under_context = set(entry["context"])
        plain = {
            key: value
            for key, value in given.items()
            if key not in OXYLABS_CLASSIC_TRANSPORT_PARAMS and key not in under_context and value is not None
        }
        context = [{"key": key, "value": given[key]} for key in entry["context"] if given.get(key) is not None]
        payload: dict[str, Any] = {OXYLABS_CLASSIC_SOURCE: entry["name"], **plain, **entry["fixed"]}
        if context:
            payload[OXYLABS_CLASSIC_CONTEXT] = [*(plain.get(OXYLABS_CLASSIC_CONTEXT) or []), *context]
        if entry["parse"]:
            payload[OXYLABS_CLASSIC_PARSE] = True
        return payload

    def _run(self, request: LiveDataRequest) -> LiveDataResponse:
        payload = self._send("POST", self.run_path, json=self._build_payload(request))
        return to_response(self.name, request, payload, WEB_SCRAPER_API)

    async def _arun(self, request: LiveDataRequest) -> LiveDataResponse:
        payload = await self._asend("POST", self.run_path, json=self._build_payload(request))
        return to_response(self.name, request, payload, WEB_SCRAPER_API)

    def sources(self) -> list[LiveDataSource]:
        """Every source, in the catalogue's order."""
        return [listed(self.name, entry) for entry in classic_sources().values()]

    def _list(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._paged(
            list(classic_sources().values()), lambda entry: listed(self.name, entry), limit=limit, offset=offset
        )

    async def _alist(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._list(limit=limit, offset=offset)

    def _get(self, name: str) -> LiveDataSource:
        entry = self._entry(name)
        return listed(self.name, entry).model_copy(update={"raw": entry})

    async def _aget(self, name: str) -> LiveDataSource:
        return self._get(name)
