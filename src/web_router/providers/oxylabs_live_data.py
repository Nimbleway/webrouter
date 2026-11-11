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

A run asks for ``output: ["json"]`` -- the scraper's parsed fields, under
``results[].json`` -- and sends the inputs as given. A scraper Oxylabs runs
only as a job (``chatgpt``, ``gemini``, ``perplexity``...) is submitted to its
async path and polled until it is done, for up to five minutes; one still
running after that raises with the job's id, so the result can be fetched
later. A page that was not fetched, or that came back with no fields, raises.
A response's ``metadata.timestamp`` is kept in ``extra`` rather than reported
as ``fetched_at``: the docs call it a Unix timestamp, not the time of the
fetch.

This is Oxylabs' Web API, authenticated with ``OXYLABS_WEB_API_KEY`` as a
bearer token -- not the Realtime API that Oxylabs' extract calls with
``OXYLABS_USERNAME`` and ``OXYLABS_PASSWORD``. Written from
https://developers.oxylabs.io/products/web-api (endpoint discovery, dedicated
scrapers, delivery modes, troubleshooting), read on 2026-11-10. **Not yet
verified live**: no Web API key was available.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, ClassVar

from web_router.constants import PROVIDER_OXYLABS
from web_router.constants_live_data_vendors import (
    ENV_OXYLABS_WEB_API_KEY,
    MSG_OXYLABS_FAULTED,
    MSG_OXYLABS_NO_FIELDS,
    MSG_OXYLABS_NOT_FETCHED,
    MSG_OXYLABS_POLL_FAILED,
    MSG_OXYLABS_STILL_RUNNING,
    OXYLABS_API_KEY_PREFIX,
    OXYLABS_ASYNC,
    OXYLABS_ASYNC_PREFIX,
    OXYLABS_BATCH,
    OXYLABS_BATCH_PREFIX,
    OXYLABS_DOCS_URL,
    OXYLABS_FAILED_FETCH_FLOOR,
    OXYLABS_JOB_DEADLINE_MARGIN,
    OXYLABS_JOB_DONE,
    OXYLABS_JOB_FAULTED,
    OXYLABS_JOB_POLL_SECONDS,
    OXYLABS_JOB_WAIT_SECONDS,
    OXYLABS_OUTPUT,
    OXYLABS_SCRAPE_PREFIX,
    OXYLABS_SCRAPERS_PATH,
    OXYLABS_SYNC,
    OXYLABS_TRANSPORT_PARAMS,
    OXYLABS_WEB_API_BASE_URL,
)
from web_router.exceptions import ProviderError
from web_router.exceptions_live_data import LiveDataJobError, LiveDataSourceNotFoundError
from web_router.providers._deadline import current_deadline, job_deadline
from web_router.providers._http_live_data import HttpLiveDataProvider
from web_router.providers._utils import resolve_setting
from web_router.providers.oxylabs_options import input_schema
from web_router.types_live_data import (
    LIVE_DATA_PARAMETERS,
    LiveDataRequest,
    LiveDataResponse,
    LiveDataSource,
    LiveDataSourcePage,
)

_MEDIA = "media"


@dataclass
class Scraper:
    """One dedicated scraper: its name, and the delivery modes the listing offers it in."""

    name: str
    modes: set[str] = field(default_factory=set)

    @property
    def realtime(self) -> bool:
        return OXYLABS_SYNC in self.modes

    @property
    def path(self) -> str:
        """Where a run is sent: the realtime endpoint, or the async one for a scraper that has no other."""
        return f"{OXYLABS_SCRAPE_PREFIX if self.realtime else OXYLABS_ASYNC_PREFIX}/{self.name}"


def _named_by(path: str) -> tuple[str, str] | None:
    """The source a listed path is, and its delivery mode; ``None`` for a path that is no scraper's."""
    for prefix, mode in (
        (OXYLABS_BATCH_PREFIX, OXYLABS_BATCH),
        (OXYLABS_ASYNC_PREFIX, OXYLABS_ASYNC),
        (OXYLABS_SCRAPE_PREFIX, OXYLABS_SYNC),
    ):
        if path.startswith(prefix + "/"):
            return path[len(prefix) + 1 :].strip("/"), mode
    return None


def catalogue(payload: Any) -> dict[str, Scraper]:
    """Every dedicated scraper in a ``GET /v1/scrapers`` answer, in its order, one per name."""
    paths = payload.get("scrapers") if isinstance(payload, dict) else None
    known: dict[str, Scraper] = {}
    for path in paths if isinstance(paths, list) else []:
        found = _named_by(path) if isinstance(path, str) else None
        if found and found[0]:
            name, mode = found
            known.setdefault(name, Scraper(name)).modes.add(mode)
    # A name listed only as a batch has no single-run endpoint to call.
    return {name: scraper for name, scraper in known.items() if scraper.modes - {OXYLABS_BATCH}}


def _source_name(source: str) -> str:
    """A source as named, or as a path: ``/v1/scrape/amazon/product`` is ``amazon/product``."""
    found = _named_by(source.strip())
    return found[0] if found else source.strip().strip("/")


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
        del username
        kwargs["api_key"] = resolve_setting(
            web_api_key, env_var=ENV_OXYLABS_WEB_API_KEY, provider=PROVIDER_OXYLABS, setting="web_api_key"
        )
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
        scraper = known.get(_source_name(source))
        if scraper is None:
            raise LiveDataSourceNotFoundError(self.name, source)
        return scraper

    # -- run ----------------------------------------------------------------

    def _run(self, request: LiveDataRequest) -> LiveDataResponse:
        scraper = self._resolve(request.source, self._known())
        with self._named(request.source):
            payload = self._send("POST", scraper.path, json=self._build_payload(request))
        # Past the submit the source is known and the job billed: a 404 now is the job's, not the source's.
        if not scraper.realtime:
            payload = self._wait(request, payload)
        return self._to_response(request, payload)

    async def _arun(self, request: LiveDataRequest) -> LiveDataResponse:
        scraper = self._resolve(request.source, await self._aknown())
        with self._named(request.source):
            payload = await self._asend("POST", scraper.path, json=self._build_payload(request))
        if not scraper.realtime:
            payload = await self._await(request, payload)
        return self._to_response(request, payload)

    @staticmethod
    def _build_payload(request: LiveDataRequest) -> dict[str, Any]:
        # Inputs and options as given, then the output that makes the answer a scraper's fields: neither can
        # ask for HTML instead, nor move the result to a webhook or a bucket this call would never read.
        given = {**request.inputs, **request.provider_options}
        return {**{k: v for k, v in given.items() if k not in OXYLABS_TRANSPORT_PARAMS}, "output": list(OXYLABS_OUTPUT)}

    def _state(self, request: LiveDataRequest, job_id: str, payload: Any) -> bool:
        """Whether a job is done; raises when it faulted."""
        body = payload if isinstance(payload, dict) else {}
        state = body.get("status") or body.get("state")
        if state == OXYLABS_JOB_FAULTED:
            first = (body.get("results") or [{}])[0]
            status = (first.get("metadata") or {}).get("status_code")
            message = MSG_OXYLABS_FAULTED.format(job_id=job_id, source=request.source, status=status)
            raise LiveDataJobError(self.name, message, job_id=job_id)
        return state == OXYLABS_JOB_DONE

    def _job_id(self, request: LiveDataRequest, payload: Any) -> str:
        job_id = str(((payload if isinstance(payload, dict) else {}).get("metadata") or {}).get("request_id") or "")
        if not job_id:
            raise ProviderError(self.name, MSG_OXYLABS_FAULTED.format(job_id="?", source=request.source, status=None))
        return job_id

    def _still_running(self, request: LiveDataRequest, job_id: str, seconds: float) -> LiveDataJobError:
        message = MSG_OXYLABS_STILL_RUNNING.format(source=request.source, seconds=seconds, job_id=job_id)
        return LiveDataJobError(self.name, message, job_id=job_id)

    def _poll_failed(self, request: LiveDataRequest, job_id: str, exc: ProviderError) -> LiveDataJobError:
        message = MSG_OXYLABS_POLL_FAILED.format(job_id=job_id, source=request.source, error=exc)
        return LiveDataJobError(self.name, message, job_id=job_id, original=exc.original)

    def _wait_until(self) -> float:
        """When the wait gives up: after :attr:`wait_seconds`, or just before the call's deadline when that is sooner.

        Stopping short of the call's deadline is what keeps the job's id: at the
        deadline itself the router raises its own error, which names no job.
        """
        until = time.monotonic() + self.wait_seconds
        call = current_deadline()
        return until if call is None else min(until, call.at - OXYLABS_JOB_DEADLINE_MARGIN)

    def _wait(self, request: LiveDataRequest, payload: Any) -> Any:
        job_id = self._job_id(request, payload)
        started, until = time.monotonic(), self._wait_until()
        # Each poll is clamped to the wait's end, so a slow one fails as a poll -- naming the job -- not as the call.
        with job_deadline(until):
            while not self._state(request, job_id, payload):
                if time.monotonic() >= until:
                    raise self._still_running(request, job_id, until - started)
                time.sleep(max(0.0, min(self.poll_seconds, until - time.monotonic())))
                if time.monotonic() >= until:
                    raise self._still_running(request, job_id, until - started)
                failed = None
                try:
                    payload = self._send("GET", f"{OXYLABS_ASYNC_PREFIX}/{job_id}")
                except ProviderError as exc:
                    failed = exc
                if failed is not None:
                    # Raised outside the except, so the deadline check reads it as the job's failure, not a timeout.
                    raise self._poll_failed(request, job_id, failed)
        return payload

    async def _await(self, request: LiveDataRequest, payload: Any) -> Any:
        job_id = self._job_id(request, payload)
        started, until = time.monotonic(), self._wait_until()
        with job_deadline(until):
            while not self._state(request, job_id, payload):
                if time.monotonic() >= until:
                    raise self._still_running(request, job_id, until - started)
                await asyncio.sleep(max(0.0, min(self.poll_seconds, until - time.monotonic())))
                if time.monotonic() >= until:
                    raise self._still_running(request, job_id, until - started)
                failed = None
                try:
                    payload = await self._asend("GET", f"{OXYLABS_ASYNC_PREFIX}/{job_id}")
                except ProviderError as exc:
                    failed = exc
                if failed is not None:
                    raise self._poll_failed(request, job_id, failed)
        return payload

    def _to_response(self, request: LiveDataRequest, payload: Any) -> LiveDataResponse:
        body = payload if isinstance(payload, dict) else {}
        results = [row for row in body.get("results") or [] if isinstance(row, dict)]
        records: list[dict[str, Any]] = []
        for result in results:
            fields, metadata = result.get("json"), result.get("metadata") or {}
            status, url = metadata.get("status_code"), metadata.get("url")
            if isinstance(status, int) and status >= OXYLABS_FAILED_FETCH_FLOOR:
                message = MSG_OXYLABS_NOT_FETCHED.format(source=request.source, status=status, url=url)
                raise ProviderError(self.name, message)
            rows = [fields] if isinstance(fields, dict) else fields if isinstance(fields, list) else []
            parsed = [row for row in rows if isinstance(row, dict) and row]
            if not parsed:
                raise ProviderError(self.name, MSG_OXYLABS_NO_FIELDS.format(source=request.source, url=url))
            records.extend(parsed)
        first = (results or [{}])[0].get("metadata") or {}
        if not records:
            raise ProviderError(self.name, MSG_OXYLABS_NO_FIELDS.format(source=request.source, url=first.get("url")))
        meta = body.get("metadata") or {}
        return LiveDataResponse(
            provider=self.name,
            source=request.source,
            inputs=dict(request.inputs),
            records=records,
            url=first.get("url"),
            status_code=first.get("status_code"),
            request_id=str(meta["request_id"]) if meta.get("request_id") is not None else None,
            extra={"metadata": meta, "results": [row.get("metadata") or {} for row in results]},
            raw=payload,
        )

    # -- catalogue ----------------------------------------------------------

    def _list(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._to_page(self._known(), limit=limit, offset=offset)

    async def _alist(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._to_page(await self._aknown(), limit=limit, offset=offset)

    def _to_page(self, known: dict[str, Scraper], *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        """One page of the scrapers. Oxylabs answers with every path at once, so the paging is done here."""
        return self._paged(list(known.values()), self._listed, limit=limit, offset=offset)

    def _listed(self, scraper: Scraper) -> LiveDataSource:
        parts = scraper.name.split("/")
        # Media scrapers name their site second: media/youtube/metadata reads YouTube.
        at = 1 if parts[0] == _MEDIA and len(parts) > 1 else 0
        only = "" if scraper.realtime else ", run as a job"
        return LiveDataSource(
            provider=self.name,
            name=scraper.name,
            description=f"Oxylabs' dedicated {scraper.name} scraper{only}: {OXYLABS_DOCS_URL}",
            domain=parts[at],
            entity_type="/".join(parts[at + 1 :]) or None,
            published=True,
            extra={"endpoint": scraper.path, "modes": sorted(scraper.modes)},
        )

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
        return self._listed(scraper).model_copy(update={"input_schema": schema, "raw": spec})
