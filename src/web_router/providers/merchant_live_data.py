"""``merchant``: a live data backend that asks a store's own agent interface, routed by a hosted registry.

A source is a store's domain. Two reads of the registry (the version pointer, then that domain's record) decide which
endpoint, whether the store needs the caller's agent profile, and which options the store has been shown to honour;
then one MCP session on the store: the handshake, then a single ``search_catalog`` call. Nothing is discovered at
request time -- no ``tools/list``, no probing -- and a missing or unproven record raises rather than falling back.

Every request, to the registry and to the store, goes through this provider's HTTP clients and their clamped
timeout. The registry names the host a store call goes to, so a route whose host is not the store's own domain, a
subdomain of it, or a platform host is refused (see :func:`~web_router.providers.merchant_record.host_allowed`).
The caller's agent profile URL identifies the caller to the store; it is not a shopper sign-in.
"""

from __future__ import annotations

import os
from typing import Any, ClassVar

from web_router.constants import MCP_ACCEPT
from web_router.constants_live_data import LIVE_DATA_HTTP_TIMEOUT
from web_router.constants_merchant import (
    DOMAIN,
    ENV_MERCHANT_AGENT_PROFILE_URL,
    MCP_VERSION_HEADER,
    MSG_BAD_INPUTS,
    MSG_REDIRECTS_REFUSED,
    MSG_RESERVED_OPTION,
    PROTOCOL_VERSION_FALLBACK,
    PROVIDER_MERCHANT,
    REGISTRY_URL,
    RESERVED_OPTIONS,
)
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.exceptions_live_data import LiveDataSourceNotFoundError
from web_router.providers._http_live_data import HttpLiveDataProvider
from web_router.providers.merchant_mcp import (
    ensure_ok,
    envelope,
    initialize,
    negotiated_version,
    products,
    result_of,
    search,
    session,
)
from web_router.providers.merchant_record import Plan, gate, input_schema, plan_for
from web_router.providers.merchant_registry import RegistryReader
from web_router.types_errors import ERROR_KIND_BAD_REQUEST
from web_router.types_live_data import (
    LIVE_DATA_PARAMETERS,
    LiveDataRequest,
    LiveDataResponse,
    LiveDataSource,
    LiveDataSourcePage,
)
from web_router.types_merchant import MerchantInputs


class MerchantLiveDataProvider(HttpLiveDataProvider, RegistryReader):
    """Catalog search on a store's own UCP-over-MCP endpoint, chosen by a hosted registry record."""

    name: ClassVar[str] = PROVIDER_MERCHANT
    supported_parameters: ClassVar[frozenset[str]] = LIVE_DATA_PARAMETERS
    base_url: ClassVar[str] = REGISTRY_URL
    run_path: ClassVar[str] = "tools/call"
    key_argument: ClassVar[str] = "agent_profile_url"
    """The setting that makes the backend usable without being named, as another backend's key does."""

    def __init__(
        self,
        *,
        agent_profile_url: str | None = None,
        registry_url: str | None = None,
        timeout: float = LIVE_DATA_HTTP_TIMEOUT,
        **kwargs: Any,
    ) -> None:
        """
        Args:
            agent_profile_url: The caller's own agent profile, a document the caller hosts. Falls back to
                ``WEB_ROUTER_MERCHANT_AGENT_PROFILE_URL``.
            registry_url: Where the registry is published; the default is the hosted one.
            timeout: Seconds per request, to the registry and to the store.
        """
        if kwargs.get("follow_redirects"):  # the base class pins redirects off only for a provider that holds a key
            raise ConfigurationError(MSG_REDIRECTS_REFUSED)
        url = (registry_url or REGISTRY_URL).rstrip("/") + "/"
        super().__init__(base_url=url, timeout=timeout, **kwargs)
        profile = (agent_profile_url or os.environ.get(ENV_MERCHANT_AGENT_PROFILE_URL) or "").strip()
        self.agent_profile_url = profile or None
        self._init_registry()

    # -- run -----------------------------------------------------------------

    def _run(self, request: LiveDataRequest) -> LiveDataResponse:
        domain, inputs = self._checked(request)
        snapshot_id = self._snapshot()
        plan = plan_for(self._record(domain, snapshot_id), domain, snapshot_id)
        gate(plan, inputs, self.agent_profile_url)
        version = plan.protocol_version or PROTOCOL_VERSION_FALLBACK
        headers = {"Accept": MCP_ACCEPT, MCP_VERSION_HEADER: version}
        opened = self._client.post(plan.endpoint, json=initialize(version), headers=headers, **self._clamped())
        agreed = negotiated_version(result_of(opened), version)
        headers = {**headers, MCP_VERSION_HEADER: agreed, **session(opened)}
        done = envelope(None, "notifications/initialized", {})
        ensure_ok(self._client.post(plan.endpoint, json=done, headers=headers, **self._clamped()))
        call = search(inputs, self.agent_profile_url, request.provider_options)
        reply = self._client.post(plan.endpoint, json=call, headers=headers, **self._clamped())
        return self._response(plan, request, agreed, result_of(reply))

    async def _arun(self, request: LiveDataRequest) -> LiveDataResponse:
        domain, inputs = self._checked(request)
        snapshot_id = await self._asnapshot()
        plan = plan_for(await self._arecord(domain, snapshot_id), domain, snapshot_id)
        gate(plan, inputs, self.agent_profile_url)
        version = plan.protocol_version or PROTOCOL_VERSION_FALLBACK
        headers = {"Accept": MCP_ACCEPT, MCP_VERSION_HEADER: version}
        opened = await self._async_client.post(
            plan.endpoint, json=initialize(version), headers=headers, **self._clamped()
        )
        agreed = negotiated_version(result_of(opened), version)
        headers = {**headers, MCP_VERSION_HEADER: agreed, **session(opened)}
        done = envelope(None, "notifications/initialized", {})
        ensure_ok(await self._async_client.post(plan.endpoint, json=done, headers=headers, **self._clamped()))
        call = search(inputs, self.agent_profile_url, request.provider_options)
        reply = await self._async_client.post(plan.endpoint, json=call, headers=headers, **self._clamped())
        return self._response(plan, request, agreed, result_of(reply))

    def _checked(self, request: LiveDataRequest) -> tuple[str, MerchantInputs]:
        """The store and the validated inputs; a name that is not a host, a bad input or a reserved option raises."""
        domain = request.source.strip().lower()
        if not DOMAIN.match(domain):
            raise LiveDataSourceNotFoundError(self.name, request.source, hint=" A source is a store's domain.")
        if clash := sorted(set(request.provider_options) & RESERVED_OPTIONS):
            raise ProviderError(self.name, MSG_RESERVED_OPTION.format(keys=clash), kind=ERROR_KIND_BAD_REQUEST)
        try:
            return domain, MerchantInputs.model_validate(request.inputs)
        except ValueError as exc:
            raise ProviderError(
                self.name, MSG_BAD_INPUTS.format(domain=domain, reason=str(exc)[:300]), kind=ERROR_KIND_BAD_REQUEST
            ) from exc

    def _response(self, plan: Plan, request: LiveDataRequest, agreed: str, result: dict[str, Any]) -> LiveDataResponse:
        return LiveDataResponse(
            provider=self.name,
            source=plan.domain,
            inputs=dict(request.inputs),
            records=products(plan.domain, result),
            url=plan.endpoint,
            raw=[result],
            extra={
                "snapshot_id": plan.snapshot_id,
                "protocol_version": agreed,
                "evidence": plan.evidence,
                "observed_at": plan.observed_at,
                "production_ready": plan.production_ready,
                "registry_search_term": plan.search_term,
            },
        )

    # -- catalogue -----------------------------------------------------------

    def _list(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._page(self._index(self._snapshot()), limit=limit, offset=offset)

    async def _alist(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._page(await self._aindex(await self._asnapshot()), limit=limit, offset=offset)

    def _page(self, sites: list[Any], *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        """The stores the registry holds profile-backed tests for; a store's record says whether it can be run."""
        tested = [
            s
            for s in sites
            if isinstance(s, dict)
            and s.get("commerce_tests")
            and isinstance(s.get("domain"), str)
            and DOMAIN.match(s["domain"])
        ]
        return self._paged(tested, lambda site: self._listed(site), limit=limit, offset=offset)

    def _listed(self, site: dict[str, Any]) -> LiveDataSource:
        return LiveDataSource(
            provider=self.name,
            name=site["domain"],
            description=f"{site.get('platform', 'unclassified')} store's own agent interface",
            domain=site["domain"],
            entity_type="product",
        )

    def _get(self, name: str) -> LiveDataSource:
        domain = self._domain(name)
        snapshot_id = self._snapshot()
        return self._described(plan_for(self._record(domain, snapshot_id), domain, snapshot_id))

    async def _aget(self, name: str) -> LiveDataSource:
        domain = self._domain(name)
        snapshot_id = await self._asnapshot()
        return self._described(plan_for(await self._arecord(domain, snapshot_id), domain, snapshot_id))

    def _domain(self, name: str) -> str:
        domain = name.strip().lower()
        if not DOMAIN.match(domain):
            raise LiveDataSourceNotFoundError(self.name, name, hint=" A source is a store's domain.")
        return domain

    def _described(self, plan: Plan) -> LiveDataSource:
        return LiveDataSource(
            provider=self.name,
            name=plan.domain,
            description="the store's own catalog search; options appear where the registry has a passing test",
            domain=plan.domain,
            entity_type="product",
            input_schema=input_schema(plan),
            version=plan.snapshot_id,
        )
