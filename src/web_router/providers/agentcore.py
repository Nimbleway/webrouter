"""AgentCore Web Search provider, through the caller's own AgentCore Gateway.

Amazon publishes no direct search API: the caller creates an AgentCore Gateway,
adds the ``web-search`` connector as a target, and the gateway serves it as an
MCP tool. Set ``AGENTCORE_GATEWAY_URL`` to the gateway's URL; the AWS
credential comes from botocore's usual chain -- a profile, a role, the
``AWS_*`` variables -- and every request is SigV4-signed for the gateway's
region, read from its hostname. Setup, with a teardown: ``docs/agentcore-setup.md``.

The gateway names its tool after the target, ``<target>___WebSearch``, so the
first search lists the gateway's tools once and keeps the one with that suffix.
The same listing says whether the target takes filters: the connector accepts
them from version 1.2.0, and on an earlier target ``include_domains`` and
``exclude_domains`` are refused rather than sent to be ignored. A date range
is AgentCore's own ``filters.publishedDateFilter``, passed through
``provider_options``; it merges with the mapped domain filter.

Results must be shown with their citations, and may not be stored in bulk or
used to build an index (AWS's acceptable-use terms for the tool).

Install with the ``aws`` extra, which adds botocore.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Generator
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session, get_session

from web_router.constants import (
    ENV_AGENTCORE_GATEWAY_URL,
    MCP_JSONRPC_VERSION,
    MSG_MCP_NO_RESULT,
    MSG_REDIRECTS_WITH_CREDENTIAL,
    PROVIDER_AGENTCORE,
)
from web_router.constants_agentcore import (
    AGENTCORE_DEFAULT_PATH,
    AGENTCORE_DOMAIN_FIELDS,
    AGENTCORE_DOMAIN_FILTER_FIELD,
    AGENTCORE_FILTERS_FIELD,
    AGENTCORE_HOST_PATTERN,
    AGENTCORE_MAX_QUERY_CHARS,
    AGENTCORE_MAX_RESULTS,
    AGENTCORE_PARAM_NAMES,
    AGENTCORE_SETUP_DOC,
    AGENTCORE_SIGNING_SERVICE,
    AGENTCORE_TOOL_SUFFIX,
    AGENTCORE_UNSIGNED_HEADER,
    ENV_AWS_REGION,
    MSG_AGENTCORE_BAD_URL,
    MSG_AGENTCORE_CURSOR_REPEATED,
    MSG_AGENTCORE_FILTER_CLASH,
    MSG_AGENTCORE_NO_CREDENTIALS,
    MSG_AGENTCORE_NO_FILTERS,
    MSG_AGENTCORE_NO_REGION,
    MSG_AGENTCORE_QUERY_TOO_LONG,
    MSG_AGENTCORE_TOO_MANY_RESULTS,
    MSG_AGENTCORE_TOOL,
)
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.providers._http import HttpSearchProvider
from web_router.providers._mcp import McpSearchMixin
from web_router.providers._utils import as_snippet, build_payload, compact
from web_router.types import SearchRequest, SearchResponse, SearchResult

_HOST = re.compile(AGENTCORE_HOST_PATTERN)
_MAPPED_RESULT_FIELDS = frozenset({"text", "url", "title", "publishedDate"})


class _SigV4(httpx.Auth):
    """Signs each request for the gateway's region, with credentials resolved on first use.

    Resolved late so a provider can be built -- by ``auto``, by a test -- on a
    machine with no AWS credential, and fail only when it is actually called.
    Frozen per request, so a refreshable credential (SSO, an assumed role) is
    renewed by botocore rather than going stale inside a long-lived router.
    """

    requires_request_body = True

    def __init__(self, session: Session, host: str) -> None:
        self._session = session
        self._host = host
        self._credentials: Any = None
        self._region: str | None = None

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        if self._credentials is None:
            self._credentials = self._session.get_credentials()
            if self._credentials is None:
                raise ConfigurationError(MSG_AGENTCORE_NO_CREDENTIALS.format(doc=AGENTCORE_SETUP_DOC))
        headers = {name: value for name, value in request.headers.items() if name.lower() != AGENTCORE_UNSIGNED_HEADER}
        signed = AWSRequest(method=request.method, url=str(request.url), data=request.content, headers=headers)
        SigV4Auth(
            self._credentials.get_frozen_credentials(), AGENTCORE_SIGNING_SERVICE, self._signing_region()
        ).add_auth(signed)
        request.headers.update(dict(signed.headers.items()))
        yield request

    def _signing_region(self) -> str:
        if self._region is None:
            match = _HOST.match(self._host)
            region = (
                match["region"]
                if match
                else os.environ.get(ENV_AWS_REGION) or self._session.get_config_variable("region")
            )
            if not region:
                raise ConfigurationError(MSG_AGENTCORE_NO_REGION.format(host=self._host, doc=AGENTCORE_SETUP_DOC))
            self._region = region
        return self._region


class AgentCoreProvider(McpSearchMixin, HttpSearchProvider):
    """Routes searches to AgentCore Web Search on the caller's gateway.

    Each hit is a passage of its page chosen for the query, with its URL,
    title and publication date; the passage is the content, and
    ``description`` is its head. AgentCore states no score.
    """

    name: ClassVar[str] = PROVIDER_AGENTCORE
    supported_parameters: ClassVar[frozenset[str]] = frozenset(AGENTCORE_PARAM_NAMES) | frozenset(
        AGENTCORE_DOMAIN_FIELDS
    )
    # Both replaced per instance from the gateway URL and its tool listing.
    base_url: ClassVar[str] = ""
    search_path: ClassVar[str] = AGENTCORE_DEFAULT_PATH
    mcp_tool: ClassVar[str] = ""

    def __init__(self, *, gateway_url: str | None = None, **kwargs: Any) -> None:
        """
        Args:
            gateway_url: The gateway's MCP URL, as ``CreateGateway`` returns it.
                Falls back to ``AGENTCORE_GATEWAY_URL``.
            **kwargs: As :class:`~web_router.providers.HttpSearchProvider`. An
                ``api_key`` is ignored: the gateway takes an AWS signature.
        """
        # Checked when called, not here: a provider is built by auto, or by a
        # test, on machines with no gateway at all.
        self._gateway_url = (gateway_url or os.environ.get(ENV_AGENTCORE_GATEWAY_URL) or "").strip()
        try:
            parts = urlsplit(self._gateway_url)
            self._host = parts.hostname or ""
        except ValueError:  # an unbalanced bracket in the host; reported at call time
            parts = urlsplit("")
            self._host = ""
        self._is_https = parts.scheme == "https"
        # Split so httpx does not join the path onto the host with a trailing
        # slash, which the gateway does not serve.
        self.search_path = parts.path or AGENTCORE_DEFAULT_PATH  # type: ignore[misc]
        self._takes_filters = False
        # The base class pins redirects off only for a provider with a key. This
        # one's credential is the signature, and its session token is a header
        # httpx would carry to another host, so the pin applies here too.
        if kwargs.get("follow_redirects"):
            raise ConfigurationError(MSG_REDIRECTS_WITH_CREDENTIAL.format(provider=self.name))
        auth = _SigV4(get_session(), self._host)
        super().__init__(base_url=f"{parts.scheme}://{parts.netloc}", auth=auth, **kwargs)

    # -- The request --------------------------------------------------------

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        # filters is set from both sides -- the mapped domains and a date range
        # in provider_options -- so it is merged here rather than left to
        # build_payload, whose last-wins update would drop the domains.
        options = dict(request.provider_options)
        filters = dict(options.pop(AGENTCORE_FILTERS_FIELD, None) or {})
        payload = build_payload(
            request.model_copy(update={"provider_options": options}), AGENTCORE_PARAM_NAMES, query=request.query
        )
        domains = compact({theirs: getattr(request, ours) for ours, theirs in AGENTCORE_DOMAIN_FIELDS.items()})
        if domains:
            given = dict(filters.get(AGENTCORE_DOMAIN_FILTER_FIELD) or {})
            clash = sorted(set(given) & set(domains))
            if clash:
                keys = ", ".join(f"{AGENTCORE_DOMAIN_FILTER_FIELD}.{key}" for key in clash)
                raise ConfigurationError(MSG_AGENTCORE_FILTER_CLASH.format(keys=keys))
            filters[AGENTCORE_DOMAIN_FILTER_FIELD] = {**given, **domains}
        if filters:
            payload[AGENTCORE_FILTERS_FIELD] = filters
        return payload

    def _check_limits(self, request: SearchRequest) -> None:
        """Refuse what the tool would, before any call -- the first one lists the gateway's tools."""
        if len(request.query) > AGENTCORE_MAX_QUERY_CHARS:
            raise ProviderError(
                self.name,
                MSG_AGENTCORE_QUERY_TOO_LONG.format(limit=AGENTCORE_MAX_QUERY_CHARS, length=len(request.query)),
            )
        if request.max_results is not None and request.max_results > AGENTCORE_MAX_RESULTS:
            raise ProviderError(
                self.name, MSG_AGENTCORE_TOO_MANY_RESULTS.format(limit=AGENTCORE_MAX_RESULTS, value=request.max_results)
            )

    # -- The call -----------------------------------------------------------

    def _search(self, request: SearchRequest) -> SearchResponse:
        if self._needs_tool(request):
            listing = self._list_tools()
            body = next(listing)
            with contextlib.suppress(StopIteration):
                while True:
                    body = listing.send(self._client.post(self.search_path, json=body, **self._clamped()))
        self._refuse_filters(request)
        return super()._search(request)

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        if self._needs_tool(request):
            listing = self._list_tools()
            body = next(listing)
            with contextlib.suppress(StopIteration):
                while True:
                    body = listing.send(await self._async_client.post(self.search_path, json=body, **self._clamped()))
        self._refuse_filters(request)
        return await super()._asearch(request)

    def _needs_tool(self, request: SearchRequest) -> bool:
        """Whether this call must list the gateway's tools first, once the request and the gateway pass."""
        self._check_limits(request)
        if self.mcp_tool:
            return False
        self._check_gateway()
        return True

    def _list_tools(self) -> Generator[dict[str, Any], httpx.Response, None]:
        """Page through ``tools/list``: yields each request body, is sent each reply, learns the tool at the end.

        One copy of the paging for both paths, which differ only in the client
        that sends what this yields. A cursor seen before stops it: a gateway
        that repeats one would otherwise keep the search listing forever.
        """
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            result = self._rpc_result((yield self._list_call(cursor)))
            tools += result.get("tools") or []
            cursor = result.get("nextCursor") or None
            if cursor is None:
                break
            cursor = str(cursor)
            if cursor in seen:
                raise ProviderError(self.name, MSG_AGENTCORE_CURSOR_REPEATED.format(cursor=cursor))
            seen.add(cursor)
        self._learn_tool(tools)

    def _check_gateway(self) -> None:
        if not self._gateway_url:
            raise ConfigurationError.missing_setting(
                self.name, env_var=ENV_AGENTCORE_GATEWAY_URL, setting="gateway_url"
            )
        if not (self._is_https and self._host):
            raise ConfigurationError(MSG_AGENTCORE_BAD_URL.format(url=self._gateway_url, doc=AGENTCORE_SETUP_DOC))

    @staticmethod
    def _list_call(cursor: str | None) -> dict[str, Any]:
        return {
            "jsonrpc": MCP_JSONRPC_VERSION,
            "id": 1,
            "method": "tools/list",
            "params": compact({"cursor": cursor}),
        }

    def _learn_tool(self, tools: list[dict[str, Any]]) -> None:
        matches = [tool for tool in tools if str(tool.get("name", "")).endswith(AGENTCORE_TOOL_SUFFIX)]
        if len(matches) != 1:
            names = ", ".join(str(tool.get("name")) for tool in matches) or "none"
            raise ConfigurationError(
                MSG_AGENTCORE_TOOL.format(
                    count=len(matches), suffix=AGENTCORE_TOOL_SUFFIX, names=names, doc=AGENTCORE_SETUP_DOC
                )
            )
        tool = matches[0]
        properties = (tool.get("inputSchema") or {}).get("properties") or {}
        self._takes_filters = AGENTCORE_FILTERS_FIELD in properties
        self.mcp_tool = str(tool["name"])  # type: ignore[misc]

    def _refuse_filters(self, request: SearchRequest) -> None:
        """A target older than connector 1.2.0 takes no filters: say so rather than send them to be dropped.

        A ConfigurationError, not an UnsupportedParameterError: the option is
        mapped, and it is this gateway that cannot take it, so auto moves on to
        the next member rather than stopping.
        """
        if self._takes_filters:
            return
        asked = [ours for ours in AGENTCORE_DOMAIN_FIELDS if getattr(request, ours)]
        if request.provider_options.get(AGENTCORE_FILTERS_FIELD):
            asked.append(f"provider_options[{AGENTCORE_FILTERS_FIELD!r}]")
        if asked:
            raise ConfigurationError(MSG_AGENTCORE_NO_FILTERS.format(names=", ".join(asked), doc=AGENTCORE_SETUP_DOC))

    # -- The response -------------------------------------------------------

    def _unwrap(self, text: str, result: dict[str, Any]) -> dict[str, Any]:
        # structuredContent is the machine-readable copy; the text block is the
        # same results as a JSON string, or a bare list of them.
        structured = result.get("structuredContent")
        data = structured if structured is not None else self._json(text)
        hits = data.get("results") if isinstance(data, dict) else data
        if not isinstance(hits, list):
            raise ProviderError(self.name, MSG_MCP_NO_RESULT)
        # Everything the tool sent stays, so raw is the reply as answered.
        return {**data, "results": hits} if isinstance(data, dict) else {"results": hits}

    def _json(self, text: str) -> Any:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(self.name, MSG_MCP_NO_RESULT, original=exc) from exc

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        request_id = payload.get("id")
        return SearchResponse(
            provider=self.name,
            query=request.query,
            # A hit without a page -- a knowledge-graph fact, say -- is no search
            # result to cite; it stays in raw.
            results=[self._to_result(hit) for hit in payload["results"] if isinstance(hit, dict) and hit.get("url")],
            request_id=str(request_id) if request_id is not None else None,
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any]) -> SearchResult:
        text = hit.get("text") or ""
        published = hit.get("publishedDate")
        extra = {key: value for key, value in hit.items() if key not in _MAPPED_RESULT_FIELDS}
        if published is not None and not isinstance(published, str):
            extra["publishedDate"] = published
        return SearchResult(
            url=hit["url"],
            title=hit.get("title") or "",
            description=as_snippet(text),
            content=text,
            published_date=published if isinstance(published, str) else None,
            extra=extra,
        )
