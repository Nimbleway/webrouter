"""Nimble providers, backed by the official ``nimble-python`` SDK.

Search, extract, Extract Templates and research are separate classes over one
SDK client: the Search API ranks the web, the Extract API fetches a page you
already have the URL of, an Extract Template parses one kind of page into a
record, and the Web Search Agent researches a question over several turns.
Install with the ``search`` extra, ``[extract]`` -- which installs the
template class too, since a template run is the Extract API's -- or
``[research]``. All four read ``NIMBLE_API_KEY`` unless a key is passed
explicitly, or use a caller's own ``AsyncNimble`` passed as ``async_client``.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar

import httpx
from nimble_python import AsyncNimble, Nimble

from web_router.constants import (
    ENV_NIMBLE_API_KEY,
    MSG_EXTRACT_BAD_STATUS,
    MSG_NIMBLE_ASYNC_CLIENT_ONLY,
    MSG_NIMBLE_CLIENT_AND_KEY,
    MSG_NIMBLE_RESEARCH_BAD_JOB_ID,
    MSG_TEMPLATE_BAD_STATUS,
    MSG_TEMPLATE_NO_RECORD,
    MSG_TEMPLATE_PAGE_FAILED,
    MSG_TEMPLATE_PARSING_FAILED,
    NIMBLE_EXTRACT_DEFAULT_FORMAT,
    NIMBLE_EXTRACT_FORMATS,
    NIMBLE_EXTRACT_SUCCESS_STATUS,
    NIMBLE_PARAM_NAMES,
    NIMBLE_PARAMS_BY_DEPTH,
    NIMBLE_RESEARCH_AGENT_NAMES,
    NIMBLE_RESEARCH_EFFORT,
    NIMBLE_RESEARCH_JOB_SEPARATOR,
    NIMBLE_RESEARCH_SOURCE_GROUP,
    NIMBLE_RESEARCH_STATES,
    NIMBLE_RESEARCH_TEXT_OUTPUT,
    NIMBLE_RESEARCH_USE_CASE_ENRICHMENT,
    NIMBLE_RESEARCH_USE_CASE_RESEARCH,
    NIMBLE_TEMPLATE_ERROR_FIELD,
    NIMBLE_TEMPLATE_ERROR_SHAPE,
    NIMBLE_TEMPLATE_FAILED_STATUS_FLOOR,
    NIMBLE_TEMPLATE_PARSING_ERROR_STATUS,
    NIMBLE_TEMPLATE_PARSING_SUCCESS_STATUS,
    NIMBLE_TEMPLATE_RECORD_FIELD,
    NIMBLE_TEMPLATE_RECORD_SHAPE,
    NIMBLE_TEMPLATE_STATUS_FIELD,
    NIMBLE_TEMPLATE_SUCCESS_STATUS,
    PROVIDER_NIMBLE,
    RESEARCH_HTTP_TIMEOUT,
)
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.providers._utils import (
    best_text,
    build_payload,
    compact,
    model_to_dict,
    research_budget,
    resolve_api_key,
)
from web_router.providers.base import ExtractProvider, ResearchProvider, SearchProvider, TemplateProvider
from web_router.types import (
    EXTRACT_PARAMETERS,
    NORMALIZED_PARAMETERS,
    RESEARCH_PARAMETERS,
    TEMPLATE_PARAMETERS,
    Citation,
    ExtractedDocument,
    ExtractFailure,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
    ExtractTemplate,
    ResearchProgress,
    ResearchRequest,
    ResearchResponse,
    ResearchState,
    SearchRequest,
    SearchResponse,
    SearchResult,
    TemplatePage,
    TemplateRequest,
    TemplateResponse,
)


class _NimbleClients:
    """The SDK clients a Nimble provider calls through, built here or handed in by the caller.

    By default a provider builds a sync and an async client from its key and
    closes both. Given an ``async_client``, it uses that one instead -- one
    authenticated as the caller's end user, say, with its own headers and a
    connection pool shared across the caller's app. It builds no sync client,
    reads no key from the environment and never closes the caller's client.
    Options passed beside it (``timeout``, ``base_url``, ...) are laid over it
    with ``copy()``, which shares its connection pool rather than opening one.
    """

    name: ClassVar[str]
    options: dict[str, Any]
    _client: Nimble | None
    _async_client: AsyncNimble
    _owns_clients: bool

    def _connect(self, api_key: str | None, async_client: AsyncNimble | None) -> None:
        client_kwargs = compact({**self.options, "api_key": api_key})
        if async_client is None:
            self._client = Nimble(**client_kwargs)
            self._async_client = AsyncNimble(**client_kwargs)
            self._owns_clients = True
        else:
            self._client = None
            self._async_client = async_client.copy(**client_kwargs) if client_kwargs else async_client
            self._owns_clients = False

    @property
    def _sync_client(self) -> Nimble:
        """The sync client, or a clear error for a provider handed only an async one."""
        if self._client is None:
            raise ConfigurationError(MSG_NIMBLE_ASYNC_CLIENT_ONLY)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    async def aclose(self) -> None:
        if self._owns_clients:
            await self._async_client.close()


def _resolve_key(api_key: str | None, async_client: AsyncNimble | None, provider: str) -> str | None:
    """The key to build clients from: none when the caller's client carries its own.

    Raises:
        ConfigurationError: for a key passed beside a client, which would
            replace the client's credential -- an end user's, say -- with it.
    """
    if async_client is None:
        return resolve_api_key(api_key, env_var=ENV_NIMBLE_API_KEY, provider=provider)
    if api_key is not None:
        raise ConfigurationError(MSG_NIMBLE_CLIENT_AND_KEY)
    return None


class NimbleProvider(_NimbleClients, SearchProvider):
    """Routes searches to the Nimble Search API."""

    name: ClassVar[str] = PROVIDER_NIMBLE
    supported_parameters: ClassVar[frozenset[str]] = NORMALIZED_PARAMETERS

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        async_client: AsyncNimble | None = None,
        **kwargs: Any,
    ) -> None:
        key = _resolve_key(api_key, async_client, self.name)
        super().__init__(api_key=key, base_url=base_url, timeout=timeout, **kwargs)
        self._connect(key, async_client)

    def _search(self, request: SearchRequest) -> SearchResponse:
        raw = self._sync_client.search(**self._build_payload(request))
        return self._to_response(request, model_to_dict(raw))

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        raw = await self._async_client.search(**self._build_payload(request))
        return self._to_response(request, model_to_dict(raw))

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        """Translate a normalized request into Nimble ``search()`` keyword arguments."""
        depth = NIMBLE_PARAMS_BY_DEPTH[request.search_depth] if request.search_depth else {}
        return build_payload(request, NIMBLE_PARAM_NAMES, query=request.query, **depth)

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        results = []
        for item in payload.get("results") or []:
            additional = item.get("additional_data") or {}
            description = item.get("description") or ""
            results.append(
                SearchResult(
                    url=item.get("url", ""),
                    title=item.get("title") or "",
                    description=description,
                    content=best_text(item.get("content"), description),
                    published_date=additional.get("publish_date") or additional.get("published_date"),
                    extra={"metadata": item.get("metadata") or {}, **additional},
                )
            )

        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=results,
            answer=payload.get("answer"),
            request_id=payload.get("request_id"),
            raw=payload,
        )


class NimbleExtractProvider(_NimbleClients, ExtractProvider):
    """Fetches page contents through the Nimble Extract API."""

    name: ClassVar[str] = PROVIDER_NIMBLE
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_PARAMETERS
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(NIMBLE_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = NIMBLE_EXTRACT_DEFAULT_FORMAT

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        async_client: AsyncNimble | None = None,
        **kwargs: Any,
    ) -> None:
        key = _resolve_key(api_key, async_client, self.name)
        super().__init__(api_key=key, base_url=base_url, timeout=timeout, **kwargs)
        self._connect(key, async_client)

    def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        raw = self._sync_client.extract.run(**self._build_payload(request, urls))
        return self._to_response(request, urls, model_to_dict(raw))

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        raw = await self._async_client.extract.run(**self._build_payload(request, urls))
        return self._to_response(request, urls, model_to_dict(raw))

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        """Translate a normalized request into Nimble ``extract.run()`` keyword arguments."""
        return build_payload(request, url=urls[0], formats=[NIMBLE_EXTRACT_FORMATS[self.format_of(request)]])

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        # Nimble echoes the URL it resolved to, which is the better label; this
        # call's own URL stands in so a failure always names something.
        url = payload.get("url") or urls[0]
        status = payload.get("status")
        data = payload.get("data") or {}

        # Nimble answers 200 and says in `status` whether it got the page, so a
        # block or a fatal fetch error arrives as a successful call carrying no
        # content. Reporting that as an empty document would hide it.
        if status != NIMBLE_EXTRACT_SUCCESS_STATUS:
            detail = MSG_EXTRACT_BAD_STATUS.format(status=status)
            failure = ExtractFailure(url=url, error="; ".join([detail, *(payload.get("warnings") or [])]))
            return ExtractResponse(provider=self.name, failures=[failure], raw=[payload])

        body_format = self.format_of(request)
        body_field = NIMBLE_EXTRACT_FORMATS[body_format]
        document = ExtractedDocument(
            url=url,
            content=data.get(body_field) or "",
            format=body_format,
            status_code=_as_int(payload.get("status_code")),
            # Read rather than popped: `data` is `payload["data"]`, and taking
            # the body out of it would take it out of `raw` too.
            extra=compact({"metadata": payload.get("metadata"), **{k: v for k, v in data.items() if k != body_field}}),
        )

        return ExtractResponse(
            provider=self.name,
            documents=[document],
            request_id=payload.get("task_id"),
            raw=[payload],
        )


class NimbleTemplateProvider(_NimbleClients, TemplateProvider):
    """Lists, describes and runs Nimble Extract Templates.

    A template run is the Extract API's realtime endpoint with a template
    named instead of a URL: the template resolves its own inputs to the page,
    fetches it and parses it into a record. The response is the same task
    envelope an extract answers with, so a page the backend could not fetch
    is told the same way, by ``status``, and the record arrives under
    ``data.parsing``.
    """

    name: ClassVar[str] = PROVIDER_NIMBLE
    supported_parameters: ClassVar[frozenset[str]] = TEMPLATE_PARAMETERS
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(NIMBLE_EXTRACT_FORMATS)

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        async_client: AsyncNimble | None = None,
        **kwargs: Any,
    ) -> None:
        key = _resolve_key(api_key, async_client, self.name)
        super().__init__(api_key=key, base_url=base_url, timeout=timeout, **kwargs)
        self._connect(key, async_client)

    def _run(self, request: TemplateRequest) -> TemplateResponse:
        raw = self._sync_client.extract.templates.run(**self._build_payload(request))
        return self._to_response(request, model_to_dict(raw))

    async def _arun(self, request: TemplateRequest) -> TemplateResponse:
        raw = await self._async_client.extract.templates.run(**self._build_payload(request))
        return self._to_response(request, model_to_dict(raw))

    def _list(self, *, limit: int | None, offset: int | None) -> TemplatePage:
        raw = self._sync_client.extract.templates.list(**compact({"limit": limit, "offset": offset}))
        return self._to_page(model_to_dict(raw))

    async def _alist(self, *, limit: int | None, offset: int | None) -> TemplatePage:
        raw = await self._async_client.extract.templates.list(**compact({"limit": limit, "offset": offset}))
        return self._to_page(model_to_dict(raw))

    def _get(self, name: str) -> ExtractTemplate:
        return self._to_template(model_to_dict(self._sync_client.extract.templates.get(name)))

    async def _aget(self, name: str) -> ExtractTemplate:
        return self._to_template(model_to_dict(await self._async_client.extract.templates.get(name)))

    def _build_payload(self, request: TemplateRequest) -> dict[str, Any]:
        """Translate a normalized request into Nimble ``extract.templates.run()`` keyword arguments.

        ``formats`` is sent only when a format was asked for: Nimble returns no
        page content without it, which is what an unset format means.
        """
        return build_payload(
            request,
            template=request.template,
            params=dict(request.params),
            formats=[NIMBLE_EXTRACT_FORMATS[request.format]] if request.format else None,
        )

    def _to_response(self, request: TemplateRequest, payload: dict[str, Any]) -> TemplateResponse:
        status = payload.get("status")
        data = payload.get("data") or {}

        # As for an extract: a block or a fatal fetch error is a 200 whose
        # `status` says so, and a record cannot have been parsed from nothing.
        if status != NIMBLE_TEMPLATE_SUCCESS_STATUS:
            detail = MSG_TEMPLATE_BAD_STATUS.format(template=request.template, status=status)
            raise ProviderError(self.name, "; ".join([detail, *(payload.get("warnings") or [])]))

        # The envelope says `success` whenever the fetch completed, a 404 page
        # included, and the template then parses the error page. What it
        # parsed is not the record the template promises.
        status_code = _as_int(payload.get("status_code"))
        if status_code is not None and status_code >= NIMBLE_TEMPLATE_FAILED_STATUS_FLOOR:
            detail = MSG_TEMPLATE_PAGE_FAILED.format(template=request.template, status=status_code)
            raise ProviderError(self.name, "; ".join([detail, *(payload.get("warnings") or [])]))

        record = self._record(request, data.get("parsing"))
        document = self._document(request, payload, data)

        return TemplateResponse(
            provider=self.name,
            template=request.template,
            params=dict(request.params),
            data=record,
            url=payload.get("url"),
            document=document,
            status_code=_as_int(payload.get("status_code")),
            request_id=payload.get("task_id"),
            # Read rather than popped, so `raw` keeps everything; the page
            # body and the record are left out because each has its own field.
            extra=compact(
                {
                    "metadata": payload.get("metadata"),
                    "pagination": payload.get("pagination"),
                    **{k: v for k, v in data.items() if k not in ("parsing", *NIMBLE_EXTRACT_FORMATS.values())},
                }
            ),
            raw=payload,
        )

    def _record(self, request: TemplateRequest, parsing: Any) -> dict[str, Any]:
        """The record under ``data.parsing``, in whichever of its three shapes Nimble sent it.

        See ``NIMBLE_TEMPLATE_PARSING_SUCCESS_STATUS``: the record may arrive
        bare, or wrapped beside a status that says it parsed, or replaced by
        one that says it did not.

        Raises:
            ProviderError: when the template reports that it failed to parse
                the page, or the page came back with no record at all.
        """
        if not isinstance(parsing, dict) or not parsing:
            raise ProviderError(self.name, MSG_TEMPLATE_NO_RECORD.format(template=request.template))
        # A wrapper is exactly the SDK's two fields and nothing else: a record
        # whose own schema names `status` and `entities` beside other fields is
        # still the record, and unwrapping it would lose the others.
        reported = parsing.get(NIMBLE_TEMPLATE_STATUS_FIELD)
        if set(parsing) == NIMBLE_TEMPLATE_ERROR_SHAPE and reported == NIMBLE_TEMPLATE_PARSING_ERROR_STATUS:
            error = parsing[NIMBLE_TEMPLATE_ERROR_FIELD]
            raise ProviderError(self.name, MSG_TEMPLATE_PARSING_FAILED.format(template=request.template, error=error))
        if (
            set(parsing) == NIMBLE_TEMPLATE_RECORD_SHAPE
            and reported == NIMBLE_TEMPLATE_PARSING_SUCCESS_STATUS
            and isinstance(parsing[NIMBLE_TEMPLATE_RECORD_FIELD], dict)
        ):
            return dict(parsing[NIMBLE_TEMPLATE_RECORD_FIELD])
        return dict(parsing)

    def _document(
        self, request: TemplateRequest, payload: dict[str, Any], data: dict[str, Any]
    ) -> ExtractedDocument | None:
        """The page beside the record, only when a format asked for it."""
        if request.format is None:
            return None
        return ExtractedDocument(
            url=payload.get("url") or "",
            content=data.get(NIMBLE_EXTRACT_FORMATS[request.format]) or "",
            format=request.format,
            status_code=_as_int(payload.get("status_code")),
        )

    def _to_page(self, payload: dict[str, Any]) -> TemplatePage:
        return TemplatePage(
            provider=self.name,
            templates=[self._to_template(item) for item in payload.get("items") or []],
            total=_as_int(payload.get("total")),
            offset=_as_int(payload.get("offset")),
            limit=_as_int(payload.get("limit")),
            raw=payload,
        )

    def _to_template(self, item: dict[str, Any]) -> ExtractTemplate:
        """One template as Nimble describes it: the published version carries everything a run needs."""
        version = item.get("published_version") or {}
        metadata = version.get("metadata") or {}
        return ExtractTemplate(
            provider=self.name,
            name=item.get("name") or "",
            description=metadata.get("description") or "",
            domain=metadata.get("domain"),
            entity_type=metadata.get("entity_type"),
            input_schema=version.get("input_schema"),
            output_schema=version.get("output_schema"),
            version=_as_int(version.get("version_number")),
            published=bool(version),
            created_at=item.get("created_at"),
            updated_at=item.get("updated_at"),
            extra=compact(
                {
                    **{k: v for k, v in item.items() if k not in _TEMPLATE_MAPPED},
                    **{k: v for k, v in metadata.items() if k not in _TEMPLATE_METADATA_MAPPED},
                    "samples": version.get("samples"),
                }
            ),
            raw=item,
        )


_TEMPLATE_MAPPED = frozenset({"name", "created_at", "updated_at", "published_version"})
_TEMPLATE_METADATA_MAPPED = frozenset({"description", "domain", "entity_type"})


class NimbleResearchProvider(_NimbleClients, ResearchProvider):
    """Runs agentic research through the Nimble Web Search Agent."""

    name: ClassVar[str] = PROVIDER_NIMBLE
    supported_parameters: ClassVar[frozenset[str]] = RESEARCH_PARAMETERS

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        async_client: AsyncNimble | None = None,
        **kwargs: Any,
    ) -> None:
        key = _resolve_key(api_key, async_client, self.name)
        super().__init__(api_key=key, base_url=base_url, timeout=timeout, **kwargs)
        self._connect(key, async_client)
        self._research_timeout = timeout if timeout is not None else self._default_timeout(async_client)

    @staticmethod
    def _default_timeout(async_client: AsyncNimble | None) -> float | None:
        """Each request's read limit when no ``timeout`` was passed: the caller's client's own, else ours.

        Every research call sets its timeout per request, which would otherwise
        replace the one the caller configured on the client they handed in.
        ``None`` is a caller's client with no read limit, which stays unlimited.
        """
        if async_client is None:
            return RESEARCH_HTTP_TIMEOUT
        configured = async_client.timeout
        if isinstance(configured, httpx.Timeout):
            return configured.read
        return float(configured) if isinstance(configured, (int, float)) else None

    def _request_timeout(self, client: Nimble | AsyncNimble) -> float | httpx.Timeout | None:
        """One request's timeout: the read limit clamped to what the caller has left.

        On a caller's client the rest of its ``httpx.Timeout`` -- connect, write,
        pool -- is kept, so only the read limit the research budget is about
        changes. A client with no read limit gets one only from that budget.
        """
        budget = research_budget(self._research_timeout if self._research_timeout is not None else math.inf)
        read = None if math.isinf(budget) else budget
        if self._owns_clients or not isinstance(configured := client.timeout, httpx.Timeout):
            return read
        return httpx.Timeout(connect=configured.connect, read=read, write=configured.write, pool=configured.pool)

    def _budgeted(self) -> Nimble:
        """The client, with this request clamped to whatever the caller has left.

        The SDK's own timeout bounds one request; it knows nothing about the
        research budget the caller asked for, so a slow run would otherwise
        outlast a short timeout by however much the two disagree.
        """
        client = self._sync_client
        return client.with_options(timeout=self._request_timeout(client))

    def _abudgeted(self) -> AsyncNimble:
        """Async counterpart of :meth:`_budgeted`."""
        return self._async_client.with_options(timeout=self._request_timeout(self._async_client))

    def _start(self, request: ResearchRequest) -> ResearchProgress:
        run = model_to_dict(self._budgeted().agents.run(**self._build_payload(request)))
        return self._progress(request, run)

    async def _astart(self, request: ResearchRequest) -> ResearchProgress:
        run = model_to_dict(await self._abudgeted().agents.run(**self._build_payload(request)))
        return await self._aprogress(request, run)

    def _fetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
        agent_id, run_id = _split_job_id(self.name, job_id)
        run = model_to_dict(self._budgeted().agents.runs.get(run_id, agent_id=agent_id))
        return self._progress(request, run)

    async def _afetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
        agent_id, run_id = _split_job_id(self.name, job_id)
        run = model_to_dict(await self._abudgeted().agents.runs.get(run_id, agent_id=agent_id))
        return await self._aprogress(request, run)

    # No `_cancel`: a run exposes get, result, list and stream_events and nothing
    # that stops one -- `agents.delete` removes the agent, not the run under it.

    def _build_payload(self, request: ResearchRequest) -> dict[str, Any]:
        """Translate a normalized request into Nimble ``agents.run()`` keyword arguments."""
        # Records to fill in are what makes a run an enrichment rather than a
        # question, and the use case is what picks the agent: a run naming none
        # creates a fresh agent every time, and Nimble refuses a run whose use
        # case disagrees with the agent it names.
        use_case = NIMBLE_RESEARCH_USE_CASE_ENRICHMENT if request.records else NIMBLE_RESEARCH_USE_CASE_RESEARCH
        return build_payload(
            request,
            input=request.query,
            agent_name=NIMBLE_RESEARCH_AGENT_NAMES[use_case],
            use_case=use_case,
            effort=NIMBLE_RESEARCH_EFFORT[request.effort] if request.effort else None,
            output_schema=request.output_schema,
            input_data=request.records,
            sources=_sources(request),
        )

    def _progress(self, request: ResearchRequest, run: dict[str, Any]) -> ResearchProgress:
        """Where this run stands, fetching its output once it has one.

        The output lives behind a second call, and asking for it before the run
        is ``completed`` is an error rather than an empty answer -- Nimble
        refuses with a 409 while it is working and a 422 once it has failed.
        """
        state = self.state_of(str(run.get("status")), NIMBLE_RESEARCH_STATES)
        if state != "completed":
            return _pending(run, state)

        agent_id, run_id = _run_ids(run)
        result = model_to_dict(self._budgeted().agents.runs.result(run_id, agent_id=agent_id))
        return ResearchProgress(job_id=_job_id(run), state=state, response=self._to_response(request, run, result))

    async def _aprogress(self, request: ResearchRequest, run: dict[str, Any]) -> ResearchProgress:
        state = self.state_of(str(run.get("status")), NIMBLE_RESEARCH_STATES)
        if state != "completed":
            return _pending(run, state)

        agent_id, run_id = _run_ids(run)
        result = model_to_dict(await self._abudgeted().agents.runs.result(run_id, agent_id=agent_id))
        return ResearchProgress(job_id=_job_id(run), state=state, response=self._to_response(request, run, result))

    def _to_response(self, request: ResearchRequest, run: dict[str, Any], result: dict[str, Any]) -> ResearchResponse:
        output = result.get("output") or {}
        content = output.get("content")
        # `type` is optional in the payload, so the content decides only when it
        # is absent: a structured output's content is an object or an array, but
        # a reported type wins over the shape -- schema-conforming JSON can be a
        # bare string.
        kind = output.get("type")
        prose = kind == NIMBLE_RESEARCH_TEXT_OUTPUT if kind is not None else isinstance(content, str)

        return ResearchResponse(
            provider=self.name,
            query=request.query,
            answer=content if prose else None,
            structured=None if prose else content,
            citations=_citations(output.get("trust") or {}),
            # `outcome` keeps its default: nothing Nimble returns reports a run
            # that stopped short of the work it was given.
            request_id=run.get("id"),
            raw=[run, result],
        )


def _as_int(value: Any) -> int | None:
    """Nimble types its status code as a number and sends it as ``200.0``."""
    return None if value is None else int(value)


def _sources(request: ResearchRequest) -> dict[str, Any] | None:
    """Domain filters as Nimble's source guidance, with only the halves that were set."""
    groups = compact(
        {
            "allow": _source_group(request.include_domains),
            "block": _source_group(request.exclude_domains),
        }
    )
    return groups or None


def _source_group(domains: list[str] | None) -> list[dict[str, Any]] | None:
    return None if domains is None else [{"title": NIMBLE_RESEARCH_SOURCE_GROUP, "domains": domains}]


def _citations(trust: dict[str, Any]) -> list[Citation]:
    """The claims' citations, then every source consulted that none of them cited.

    A claim says what its citations back -- a path into the structured answer,
    or the callout marker that appears in the prose -- which is what
    :attr:`Citation.field` carries. The sources list is the run's bibliography
    and attributes nothing, so what it adds is cited without a field.
    """
    citations: list[Citation] = []
    seen: set[tuple[str, str | None]] = set()

    for claim in trust.get("claims") or []:
        field = claim.get("path")
        if field is None and claim.get("callout") is not None:
            field = str(claim["callout"])
        for cited in claim.get("citations") or []:
            url = cited.get("url") or ""
            if (url, field) in seen:
                continue
            seen.add((url, field))
            excerpts = cited.get("excerpts") or []
            citations.append(
                Citation(
                    url=url,
                    title=cited.get("title") or "",
                    snippet=excerpts[0] if excerpts else "",
                    field=field,
                    extra=_unmapped(cited),
                )
            )

    cited_urls = {citation.url for citation in citations}
    for source in trust.get("sources") or []:
        url = source.get("url") or ""
        if url in cited_urls:
            continue
        cited_urls.add(url)
        citations.append(Citation(url=url, title=source.get("title") or "", extra=_unmapped(source)))

    return citations


def _unmapped(cited: dict[str, Any]) -> dict[str, Any]:
    """Everything a citation carries beyond the normalized fields, including every excerpt."""
    return compact({key: value for key, value in cited.items() if key not in ("url", "title")})


def _pending(run: dict[str, Any], state: ResearchState) -> ResearchProgress:
    """A run with no output to fetch: still going, or stopped without one."""
    error = run.get("error") or {}
    return ResearchProgress(job_id=_job_id(run), state=state, detail=error.get("message"))


def _job_id(run: dict[str, Any]) -> str:
    return f"{run.get('web_search_agent_id', '')}{NIMBLE_RESEARCH_JOB_SEPARATOR}{run.get('id', '')}"


def _run_ids(run: dict[str, Any]) -> tuple[str, str]:
    return str(run.get("web_search_agent_id", "")), str(run.get("id", ""))


def _split_job_id(provider: str, job_id: str) -> tuple[str, str]:
    """The agent and run a job id names, which Nimble needs both of to answer."""
    agent_id, separator, run_id = job_id.partition(NIMBLE_RESEARCH_JOB_SEPARATOR)
    if not (separator and agent_id and run_id):
        raise ProviderError(
            provider,
            MSG_NIMBLE_RESEARCH_BAD_JOB_ID.format(job_id=job_id, separator=NIMBLE_RESEARCH_JOB_SEPARATOR),
        )
    return agent_id, run_id
