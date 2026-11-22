"""Oxylabs live data under one name, over whichever of its two APIs the caller holds a credential for.

Oxylabs sells two scraping APIs with separate credentials and catalogues: the
Web Scraper API ("Classic"), with ``OXYLABS_USERNAME`` and ``OXYLABS_PASSWORD``
and sources named like ``amazon_product``
(:mod:`~web_router.providers.oxylabs_classic`), and the Web API, with
``OXYLABS_WEB_API_KEY`` and scrapers named like ``amazon/product``
(:mod:`~web_router.providers.oxylabs_live_data`). Either credential makes
``oxylabs`` a live data backend, and with both its catalogue is the two
together. A run or a description goes to the Web Scraper API when it has the
source, and to the Web API otherwise. A listing whose Web API half fails still
returns the Web Scraper API's sources, naming the failure in ``failures``.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from web_router.constants import ENV_OXYLABS_PASSWORD, ENV_OXYLABS_USERNAME, PROVIDER_OXYLABS
from web_router.constants_live_data_vendors import ENV_OXYLABS_WEB_API_KEY
from web_router.constants_oxylabs_classic import (
    LOG_OXYLABS_WEB_API_LEFT_OUT,
    MSG_OXYLABS_NO_CREDENTIALS,
    MSG_OXYLABS_ONE_BASE_URL,
    OXYLABS_WEB_API_FAILURE,
)
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.providers._http_live_data import HttpLiveDataProvider
from web_router.providers._utils import credential_given
from web_router.providers.base import LiveDataProvider
from web_router.providers.oxylabs_classic import OxylabsClassicLiveDataProvider
from web_router.providers.oxylabs_live_data import OxylabsWebApiLiveDataProvider
from web_router.types_live_data import (
    LIVE_DATA_PARAMETERS,
    LiveDataRequest,
    LiveDataResponse,
    LiveDataSource,
    LiveDataSourcePage,
)

logger = logging.getLogger(__name__)


class OxylabsLiveDataProvider(LiveDataProvider):
    """Oxylabs' live data: the Web Scraper API's parsed sources, the Web API's scrapers, or both."""

    name: ClassVar[str] = PROVIDER_OXYLABS
    supported_parameters: ClassVar[frozenset[str]] = LIVE_DATA_PARAMETERS

    key_argument: ClassVar[str] = "web_api_key"
    """Read by the router to tell a Web API key in code; the Web Scraper API's password is the plain ``api_key``."""
    apis: ClassVar[tuple[type[HttpLiveDataProvider], ...]] = (
        OxylabsClassicLiveDataProvider,
        OxylabsWebApiLiveDataProvider,
    )
    """The two APIs a call may go to, in the order a source is looked for: what a contract records."""

    def __init__(
        self,
        *,
        username: str | None = None,
        api_key: str | None = None,
        web_api_key: str | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Args:
            username: The Web Scraper API username. Falls back to ``OXYLABS_USERNAME``.
            api_key: The Web Scraper API password, as extract reads it from the
                same ``provider_config["oxylabs"]`` entry. Falls back to ``OXYLABS_PASSWORD``.
            web_api_key: The Web API key. Falls back to ``OXYLABS_WEB_API_KEY``.
            **kwargs: Passed to each API's provider: ``timeout``, the ``httpx`` options, and ``base_url``
                when only one credential is set.

        Raises:
            ConfigurationError: when neither credential is set, naming both; or when both are and so is
                ``base_url``, which cannot be both APIs' host.
        """
        super().__init__(**kwargs)
        self.classic: OxylabsClassicLiveDataProvider | None = None
        self.web_api: OxylabsWebApiLiveDataProvider | None = None
        classic = credential_given(username, ENV_OXYLABS_USERNAME) and credential_given(api_key, ENV_OXYLABS_PASSWORD)
        web_api = credential_given(web_api_key, ENV_OXYLABS_WEB_API_KEY)
        if classic and web_api and kwargs.get("base_url"):
            raise ConfigurationError(MSG_OXYLABS_ONE_BASE_URL)
        if classic:
            self.classic = OxylabsClassicLiveDataProvider(username=username, api_key=api_key, **kwargs)
        if web_api:
            self.web_api = OxylabsWebApiLiveDataProvider(web_api_key=web_api_key, **kwargs)
        if self.classic is None and self.web_api is None:
            raise ConfigurationError(MSG_OXYLABS_NO_CREDENTIALS)

    def _serving(self, source: str) -> HttpLiveDataProvider:
        """The API that runs or describes ``source``: the Web Scraper API's own, else the Web API, else a not-found."""
        classic, web_api = self.classic, self.web_api
        if classic is not None and (web_api is None or classic.knows(source)):
            return classic
        if web_api is None:
            raise ConfigurationError(MSG_OXYLABS_NO_CREDENTIALS)
        return web_api

    def _build_payload(self, request: LiveDataRequest) -> dict[str, Any]:
        """What the serving API sends for ``request``: its contract's body."""
        return self._serving(request.source)._build_payload(request)  # type: ignore[attr-defined,no-any-return]

    def _run(self, request: LiveDataRequest) -> LiveDataResponse:
        return self._serving(request.source)._run(request)

    async def _arun(self, request: LiveDataRequest) -> LiveDataResponse:
        return await self._serving(request.source)._arun(request)

    def _page(
        self, web: list[LiveDataSource] | ProviderError, *, limit: int | None, offset: int | None
    ) -> LiveDataSourcePage:
        """Both catalogues as one page; a failed Web API listing leaves its half out and is named in ``failures``.

        Raises:
            ProviderError: the Web API's, when it failed and there is no Web Scraper API to answer instead.
        """
        failures: dict[str, str] = {}
        if isinstance(web, ProviderError):
            if self.classic is None:
                raise web
            logger.warning(LOG_OXYLABS_WEB_API_LEFT_OUT, web)
            web, failures = [], {OXYLABS_WEB_API_FAILURE: str(web)}
        every = [*(self.classic.sources() if self.classic is not None else []), *web]
        page, start = HttpLiveDataProvider._local_page(every, limit=limit, offset=offset)
        total = None if failures else len(every)
        return LiveDataSourcePage(
            provider=self.name, sources=page, total=total, offset=start, limit=limit, failures=failures
        )

    def _list(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        web: list[LiveDataSource] | ProviderError = []
        if self.web_api is not None:
            try:
                web = self.web_api._list(limit=None, offset=None).sources
            except ProviderError as error:
                web = error
        return self._page(web, limit=limit, offset=offset)

    async def _alist(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        web: list[LiveDataSource] | ProviderError = []
        if self.web_api is not None:
            try:
                web = (await self.web_api._alist(limit=None, offset=None)).sources
            except ProviderError as error:
                web = error
        return self._page(web, limit=limit, offset=offset)

    def _get(self, name: str) -> LiveDataSource:
        return self._serving(name)._get(name)

    async def _aget(self, name: str) -> LiveDataSource:
        return await self._serving(name)._aget(name)

    def close(self) -> None:
        for api in (self.classic, self.web_api):
            if api is not None:
                api.close()

    async def aclose(self) -> None:
        for api in (self.classic, self.web_api):
            if api is not None:
                await api.aclose()
