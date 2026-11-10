"""Nimble providers, backed by the official ``nimble-python`` SDK.

Search, extract and research are separate classes over one SDK client: the
Search API ranks the web, the Extract API fetches a page you already have the
URL of, and the Web Search Agent researches a question over several turns.
The Extract Template class is next door in :mod:`web_router.providers.nimble_template`,
on the same clients. Install with the ``search`` extra, ``[extract]`` or
``[research]``. All of them read ``NIMBLE_API_KEY`` unless a key is passed
explicitly, or use a caller's own ``AsyncNimble`` passed as ``async_client``.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar, TypeVar

import httpx
from nimble_python import AsyncNimble, Nimble

from web_router.constants import (
    ENV_NIMBLE_API_KEY,
    JSON_SCHEMA_DEFINITIONS,
    MSG_EXTRACT_BAD_STATUS,
    MSG_NIMBLE_ASYNC_CLIENT_ONLY,
    MSG_NIMBLE_CLIENT_AND_KEY,
    MSG_NIMBLE_ENRICHMENT,
    MSG_NIMBLE_RESEARCH_BAD_JOB_ID,
    NIMBLE_ENRICHMENT_REFUSED_EFFORTS,
    NIMBLE_ENRICHMENT_SCHEMA_ROOT,
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
    PROVIDER_NIMBLE,
    RESEARCH_HTTP_TIMEOUT,
)
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.providers._clamp import clamp_timeout
from web_router.providers._deadline import current_deadline, research_budget
from web_router.providers._utils import best_text, build_payload, compact, mapped, model_to_dict, resolve_api_key
from web_router.providers.base import ExtractProvider, ResearchProvider, SearchProvider, _BaseProvider
from web_router.types import (
    EXTRACT_FORMAT_ONLY,
    RESEARCH_PARAMETERS,
    Citation,
    ExtractedDocument,
    ExtractFailure,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
    ResearchProgress,
    ResearchRequest,
    ResearchResponse,
    ResearchState,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

_ClientT = TypeVar("_ClientT", Nimble, AsyncNimble)


class _NimbleClients(_BaseProvider):
    """What the four Nimble classes share: one constructor, and the SDK clients it builds or is handed.

    Each class lists this first and its method's base second, so the
    constructor here resolves the key and builds the clients before the
    method's base sees the kwargs.

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

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        async_client: AsyncNimble | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Args:
            api_key: Nimble's key; unset, read from ``NIMBLE_API_KEY``.
            base_url: Where the SDK sends its requests; unset, the SDK's own.
            timeout: Each request's limit, in seconds; unset, the SDK's own.
            async_client: A caller's own ``AsyncNimble``, used instead of
                building clients from a key -- see the class docstring.
            **kwargs: Further SDK client options, laid over the client.

        Raises:
            ConfigurationError: for no key anywhere, or a key beside a client.
        """
        key = _resolve_key(api_key, async_client, self.name)
        super().__init__(api_key=key, base_url=base_url, timeout=timeout, **kwargs)
        self._connect(key, async_client)

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

    @staticmethod
    def _within_deadline(client: _ClientT) -> _ClientT:
        """``client`` for one call, its every limit clamped to the time a deadline in force leaves.

        The client itself with no deadline. Under one the SDK's retries are
        off as well: a retry starts a new request with the full clamped limit,
        so two of them would hold the caller past the deadline.
        """
        if current_deadline() is None:
            return client
        return client.with_options(timeout=clamp_timeout(client.timeout), max_retries=0)

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
    supported_parameters: ClassVar[frozenset[str]] = frozenset(NIMBLE_PARAM_NAMES) | {"search_depth"}

    def _search(self, request: SearchRequest) -> SearchResponse:
        raw = self._within_deadline(self._sync_client).search(**self._build_payload(request))
        return self._to_response(request, model_to_dict(raw))

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        raw = await self._within_deadline(self._async_client).search(**self._build_payload(request))
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
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_FORMAT_ONLY
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(NIMBLE_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = NIMBLE_EXTRACT_DEFAULT_FORMAT

    def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        raw = self._within_deadline(self._sync_client).extract.run(**self._build_payload(request, urls))
        return self._to_response(request, urls, model_to_dict(raw))

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        raw = await self._within_deadline(self._async_client).extract.run(**self._build_payload(request, urls))
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
        super().__init__(api_key=api_key, base_url=base_url, timeout=timeout, async_client=async_client, **kwargs)
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

    conditions: ClassVar[str | None] = MSG_NIMBLE_ENRICHMENT

    @classmethod
    def missing_parameters(cls, request: ResearchRequest) -> frozenset[str]:
        """An enrichment fills in a schema: ``records`` without one is answered with a 422."""
        return frozenset({"output_schema"}) if request.records and request.output_schema is None else frozenset()

    @classmethod
    def refused_values(cls, request: ResearchRequest) -> dict[str, Any]:
        refused = request.records and request.effort in NIMBLE_ENRICHMENT_REFUSED_EFFORTS
        return {"effort": request.effort} if refused else {}

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
            effort=mapped(NIMBLE_RESEARCH_EFFORT, request.effort),
            output_schema=_enrichment_schema(request) if request.records else request.output_schema,
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
            citations=_citations(output.get("trust") or {}, output.get("sources") or []),
            # `outcome` keeps its default: nothing Nimble returns reports a run
            # that stopped short of the work it was given.
            request_id=run.get("id"),
            raw=[run, result],
        )


def _as_int(value: Any) -> int | None:
    """Nimble types its status code as a number and sends it as ``200.0``."""
    return None if value is None else int(value)


def _enrichment_schema(request: ResearchRequest) -> dict[str, Any] | None:
    """The table Nimble's enrichment takes: one row's schema per record, unless the caller already sent a table."""
    schema = request.output_schema
    if schema is None or schema.get("type") == NIMBLE_ENRICHMENT_SCHEMA_ROOT:
        return schema
    # A nested model's definitions stay at the root, where its `#/$defs/...` references point.
    row = {key: value for key, value in schema.items() if key not in JSON_SCHEMA_DEFINITIONS}
    definitions = {key: schema[key] for key in JSON_SCHEMA_DEFINITIONS if key in schema}
    return {"type": NIMBLE_ENRICHMENT_SCHEMA_ROOT, "items": row, **definitions}


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


def _citations(trust: dict[str, Any], sources: list[dict[str, Any]]) -> list[Citation]:
    """The claims' citations, then every source consulted that none of them cited.

    A claim says what its citations back -- a path into the structured answer,
    or the callout marker that appears in the prose -- which is what
    :attr:`Citation.field` carries. The sources list is the run's bibliography
    and attributes nothing, so what it adds is cited without a field.

    A run without trust (``trust_mode="no_trust"``) sends ``trust: null`` and
    lists its bibliography in ``output.sources`` instead.
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
    for source in [*(trust.get("sources") or []), *sources]:
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
