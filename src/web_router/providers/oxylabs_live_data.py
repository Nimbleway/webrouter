"""Oxylabs live data: the Web API's dedicated scrapers -- ``amazon/product``, ``google/search`` and the rest.

A source is one of the scrape endpoints Oxylabs maintains for one kind of
page, named by its path after ``/v1/scrape/``: ``amazon/product`` is
``POST /v1/scrape/amazon/product``. The catalogue is the Web API's own list,
``GET /v1/scrapers``, read once per provider and kept, as Oxylabs advises: it
changes on their deploys, not per request. A description asks the endpoint
itself with ``OPTIONS``, which answers with every parameter's type, whether it
is required, its allowed values and default, so ``input_schema`` is the
vendor's own, not a guess. The generic ``/v1/scrape`` is not a source: it is a
page fetch, which ``extract`` covers.

A run asks for ``output: ["json"]``, the scraper's parsed fields
(:mod:`~web_router.providers.oxylabs_response`); a scraper Oxylabs runs only
as a job is submitted and polled for up to five minutes
(:mod:`~web_router.providers.oxylabs_jobs`).

This is Oxylabs' Web API, authenticated with ``OXYLABS_WEB_API_KEY`` as a
bearer token -- not the Realtime API that Oxylabs' extract calls with
``OXYLABS_USERNAME`` and ``OXYLABS_PASSWORD``. Written from
https://developers.oxylabs.io/products/web-api (endpoint discovery, dedicated
scrapers, delivery modes, troubleshooting), read on 2026-11-10. **Not yet
verified live**: no Web API key was available.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, ClassVar

from web_router.constants import ENV_OXYLABS_USERNAME, PROVIDER_OXYLABS
from web_router.constants_live_data_vendors import (
    ENV_OXYLABS_WEB_API_KEY,
    MSG_OXYLABS_JOB_RESULT_FAILED,
    MSG_OXYLABS_POLL_FAILED,
    OXYLABS_API_KEY_PREFIX,
    OXYLABS_JOB_POLL_SECONDS,
    OXYLABS_JOB_WAIT_SECONDS,
    OXYLABS_OUTPUT,
    OXYLABS_SCRAPE_PREFIX,
    OXYLABS_SCRAPERS_PATH,
    OXYLABS_TRANSPORT_PARAMS,
    OXYLABS_WEB_API_BASE_URL,
)
from web_router.constants_providers import MSG_OXYLABS_TWO_CREDENTIALS
from web_router.exceptions import ConfigurationError
from web_router.exceptions_live_data import LiveDataSourceNotFoundError
from web_router.providers._deadline import job_deadline
from web_router.providers._http_live_data import HttpLiveDataProvider
from web_router.providers._utils import resolve_setting
from web_router.providers.oxylabs_jobs import JobWait, job_id, wait_until
from web_router.providers.oxylabs_options import input_schema
from web_router.providers.oxylabs_response import to_response
from web_router.providers.oxylabs_scrapers import Scraper, catalogue, listed, source_name
from web_router.types_live_data import (
    LIVE_DATA_PARAMETERS,
    LiveDataRequest,
    LiveDataResponse,
    LiveDataSource,
    LiveDataSourcePage,
)


class OxylabsLiveDataProvider(HttpLiveDataProvider):
    """Runs the Oxylabs Web API's dedicated scrapers as live data sources."""

    name: ClassVar[str] = PROVIDER_OXYLABS
    supported_parameters: ClassVar[frozenset[str]] = LIVE_DATA_PARAMETERS

    base_url: ClassVar[str] = OXYLABS_WEB_API_BASE_URL
    scrapers_path: ClassVar[str] = OXYLABS_SCRAPERS_PATH
    run_path: ClassVar[str] = OXYLABS_SCRAPE_PREFIX + "/{source}"
    """A realtime run; a scraper with no realtime endpoint is sent to ``/v1/async/scrape/{source}`` instead."""
    env_api_key: ClassVar[str] = ENV_OXYLABS_WEB_API_KEY
    api_key_prefix: ClassVar[str] = OXYLABS_API_KEY_PREFIX
    key_argument: ClassVar[str] = "web_api_key"
    """The constructor argument that carries the key: ``api_key`` is extract's Realtime password."""

    poll_seconds: ClassVar[float] = OXYLABS_JOB_POLL_SECONDS
    wait_seconds: ClassVar[float] = OXYLABS_JOB_WAIT_SECONDS

    def __init__(self, *, web_api_key: str | None = None, username: str | None = None, **kwargs: Any) -> None:
        """
        Args:
            web_api_key: The Web API key. Falls back to ``OXYLABS_WEB_API_KEY``.
            username: Accepted and unused: it is the Realtime username Oxylabs'
                extract reads from the same ``provider_config["oxylabs"]`` entry.
            **kwargs: As :class:`~web_router.providers._http_live_data.HttpLiveDataProvider`,
                but ``api_key`` is never read: in the shared entry it is
                extract's Realtime password, which must not reach the Web API
                as a bearer token, so the Web API key has a name of its own.
        """
        try:
            kwargs["api_key"] = resolve_setting(
                web_api_key, env_var=ENV_OXYLABS_WEB_API_KEY, provider=PROVIDER_OXYLABS, setting="web_api_key"
            )
        except ConfigurationError as exc:
            # The extract credentials are set, so the caller believes Oxylabs is configured: say they are another set.
            if not (username or os.environ.get(ENV_OXYLABS_USERNAME)):
                raise
            raise ConfigurationError(f"{exc}{MSG_OXYLABS_TWO_CREDENTIALS}") from None
        super().__init__(**kwargs)
        self._catalogue: dict[str, Scraper] | None = None

    # -- names --------------------------------------------------------------

    def _known(self) -> dict[str, Scraper]:
        if self._catalogue is None:
            self._catalogue = catalogue(self._send("GET", OXYLABS_SCRAPERS_PATH))
        return self._catalogue

    async def _aknown(self) -> dict[str, Scraper]:
        if self._catalogue is None:
            self._catalogue = catalogue(await self._asend("GET", OXYLABS_SCRAPERS_PATH))
        return self._catalogue

    def _resolve(self, source: str, known: dict[str, Scraper]) -> Scraper:
        scraper = known.get(source_name(source))
        if scraper is None:
            raise LiveDataSourceNotFoundError(self.name, source)
        return scraper

    # -- run ----------------------------------------------------------------

    def _run(self, request: LiveDataRequest) -> LiveDataResponse:
        scraper = self._resolve(request.source, self._known())
        with self._named(request.source):
            payload = self._send("POST", scraper.path, json=self._build_payload(request))
        # Past the submit the source is known and the job billed: a 404 now is the job's, not the source's.
        if scraper.realtime:
            return to_response(self.name, request, payload)
        job = self._job(request, payload)
        payload = self._wait(job, payload)
        with job.naming(MSG_OXYLABS_JOB_RESULT_FAILED):
            return to_response(self.name, request, payload)

    async def _arun(self, request: LiveDataRequest) -> LiveDataResponse:
        scraper = self._resolve(request.source, await self._aknown())
        with self._named(request.source):
            payload = await self._asend("POST", scraper.path, json=self._build_payload(request))
        if scraper.realtime:
            return to_response(self.name, request, payload)
        job = self._job(request, payload)
        payload = await self._await(job, payload)
        with job.naming(MSG_OXYLABS_JOB_RESULT_FAILED):
            return to_response(self.name, request, payload)

    @staticmethod
    def _build_payload(request: LiveDataRequest) -> dict[str, Any]:
        # Inputs and options as given, then the output that makes the answer a scraper's fields: neither can
        # ask for HTML instead, nor move the result to a webhook or a bucket this call would never read.
        given = {**request.inputs, **request.provider_options}
        return {**{k: v for k, v in given.items() if k not in OXYLABS_TRANSPORT_PARAMS}, "output": list(OXYLABS_OUTPUT)}

    def _job(self, request: LiveDataRequest, payload: Any) -> JobWait:
        return JobWait(
            self.name, request, job_id(self.name, request, payload), wait_until(self.wait_seconds), self.poll_seconds
        )

    def _wait(self, job: JobWait, payload: Any) -> Any:
        with job_deadline(job.until):
            while not job.done(payload):
                time.sleep(job.pause())
                job.check()
                with job.naming(MSG_OXYLABS_POLL_FAILED):
                    payload = self._send("GET", job.path)
        return payload

    async def _await(self, job: JobWait, payload: Any) -> Any:
        with job_deadline(job.until):
            while not job.done(payload):
                await asyncio.sleep(job.pause())
                job.check()
                with job.naming(MSG_OXYLABS_POLL_FAILED):
                    payload = await self._asend("GET", job.path)
        return payload

    # -- catalogue ----------------------------------------------------------

    def _list(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._to_page(self._known(), limit=limit, offset=offset)

    async def _alist(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._to_page(await self._aknown(), limit=limit, offset=offset)

    def _to_page(self, known: dict[str, Scraper], *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        """One page of the scrapers. Oxylabs answers with every path at once, so the paging is done here."""
        return self._paged(list(known.values()), lambda scraper: listed(self.name, scraper), limit=limit, offset=offset)

    def _get(self, name: str) -> LiveDataSource:
        scraper = self._resolve(name, self._known())
        with self._named(name):
            spec = self._send("OPTIONS", scraper.path)
        return self._described(scraper, spec)

    async def _aget(self, name: str) -> LiveDataSource:
        scraper = self._resolve(name, await self._aknown())
        with self._named(name):
            spec = await self._asend("OPTIONS", scraper.path)
        return self._described(scraper, spec)

    def _described(self, scraper: Scraper, spec: Any) -> LiveDataSource:
        schema = input_schema(spec, leave_out=OXYLABS_TRANSPORT_PARAMS) if isinstance(spec, dict) else None
        return listed(self.name, scraper).model_copy(update={"input_schema": schema, "raw": spec})
