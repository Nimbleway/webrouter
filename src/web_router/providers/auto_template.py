"""The Extract Template chain of ``auto``: the keyed backends, and nothing after them.

Built on the same pieces as the other chains in :mod:`web_router.providers.auto`
and kept apart from them so that module stops growing a copy of the chain
skeleton for every method.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Mapping
from typing import Any, ClassVar, TypeVar

from web_router.constants import (
    AUTO_CHAIN_TEMPLATE,
    AUTO_TEMPLATE_FORMATS,
    AUTO_TEMPLATE_PREFERRED,
    AUTO_TEMPLATE_SUPPORTED_PARAMETERS,
    MSG_AUTO_TEMPLATE_EXHAUSTED,
    MSG_AUTO_TEMPLATE_NO_CAPABLE_BACKEND,
    MSG_AUTO_TEMPLATE_NO_CREDENTIAL,
    MSG_AUTO_TEMPLATE_SKIPPED,
    MSG_AUTO_UNFORMATTED,
    MSG_AUTO_UNMAPPED,
    PROVIDER_AUTO,
    TEMPLATE_ENV_BY_PROVIDER,
)
from web_router.exceptions import (
    ProviderNotInstalledError,
    UnsupportedFormatError,
    UnsupportedParameterError,
    WebRouterError,
)
from web_router.providers._chain import (
    _RECOVERABLE,
    _ChainSpec,
    _keyed_chain,
    _member_options,
    _members_and_chain,
    _no_backend,
    _nothing_started,
    _quoted,
)
from web_router.providers.base import TemplateProvider
from web_router.types import ExtractFormat, ExtractTemplate, TemplatePage, TemplateRequest, TemplateResponse

_T = TypeVar("_T")


class AutoTemplateProvider(TemplateProvider):
    """Runs, lists and describes Extract Templates through the first backend that answers.

    Every member needs a credential, so the chain is the backends you hold a
    key for and nothing after them. A template's name is the backend's own,
    so a run, a listing and a description all go to the first member that
    serves the call. A listing or a description that fails falls through as
    a search does, since neither bills; a run falls through only on a
    failure that proves nothing was fetched, because a page the first member
    fetched and then failed to parse is already paid for, and a second
    member would fetch it again.
    """

    name: ClassVar[str] = PROVIDER_AUTO
    supported_parameters: ClassVar[frozenset[str]] = AUTO_TEMPLATE_SUPPORTED_PARAMETERS
    supported_formats: ClassVar[frozenset[ExtractFormat]] = AUTO_TEMPLATE_FORMATS

    def __init__(
        self,
        *,
        chain: _ChainSpec | None = None,
        members_config: Mapping[str, Mapping[str, Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Args:
            chain: Backends to try, in order, as on :class:`AutoProvider`; keyed
                by method, this reads the ``"template"`` entry.
            **kwargs: Ignored, as on :class:`AutoProvider`.
        """
        super().__init__(**kwargs)
        self._members_config, self._chain = _members_and_chain(
            chain, members_config, AUTO_CHAIN_TEMPLATE, self.default_chain
        )
        self._members: dict[int, TemplateProvider] = {}

    @staticmethod
    def default_chain(in_code: Collection[str] = ()) -> tuple[tuple[str, dict[str, Any]], ...]:
        """The backends you have credentials for. Nothing follows them: a run bills a fetch."""
        from web_router.registry import configured_template_providers

        return _keyed_chain(AUTO_TEMPLATE_PREFERRED, set(configured_template_providers()) | set(in_code))

    @property
    def members(self) -> tuple[str, ...]:
        """The backends this instance would try, in order, as on :class:`AutoProvider`."""
        return tuple(provider_name for provider_name, _ in self._chain)

    def _member(self, position: int) -> TemplateProvider:
        """Build and cache one member. Imported here so the chain stays lazy."""
        from web_router.registry import create_template_provider

        if position not in self._members:
            provider_name, options = self._chain[position]
            self._members[position] = create_template_provider(
                provider_name, **_member_options(self._members_config, provider_name, options)
            )
        return self._members[position]

    def _run(self, request: TemplateRequest) -> TemplateResponse:
        failures: dict[str, WebRouterError] = {}
        for position, provider_name in self._eligible(request):
            try:
                return self._member(position).run(request)
            except _RECOVERABLE as exc:
                # A parse that failed, a 5xx or a read timeout may come after
                # the page was fetched and billed, so only a failure that
                # proves nothing was may try the next member.
                if not _nothing_started(exc):
                    raise
                failures[provider_name] = exc
        raise self._no_backend(failures)

    async def _arun(self, request: TemplateRequest) -> TemplateResponse:
        failures: dict[str, WebRouterError] = {}
        for position, provider_name in self._eligible(request):
            try:
                return await self._member(position).arun(request)
            except _RECOVERABLE as exc:
                if not _nothing_started(exc):
                    raise
                failures[provider_name] = exc
        raise self._no_backend(failures)

    def _list(self, *, limit: int | None, offset: int | None) -> TemplatePage:
        return self._first_answering(lambda member: member.templates(limit=limit, offset=offset))

    async def _alist(self, *, limit: int | None, offset: int | None) -> TemplatePage:
        return await self._afirst_answering(lambda member: member.atemplates(limit=limit, offset=offset))

    def _get(self, name: str) -> ExtractTemplate:
        return self._first_answering(lambda member: member.template(name))

    async def _aget(self, name: str) -> ExtractTemplate:
        return await self._afirst_answering(lambda member: member.atemplate(name))

    def _first_answering(self, call: Callable[[TemplateProvider], _T]) -> _T:
        """``call`` on each member in turn until one answers.

        A listing or a description bills nothing, so any recoverable failure
        falls through to the next member.
        """
        failures: dict[str, WebRouterError] = {}
        for position, (provider_name, _) in enumerate(self._chain):
            try:
                return call(self._member(position))
            except _RECOVERABLE as exc:
                failures[provider_name] = exc
        raise self._no_backend(failures)

    async def _afirst_answering(self, call: Callable[[TemplateProvider], Awaitable[_T]]) -> _T:
        failures: dict[str, WebRouterError] = {}
        for position, (provider_name, _) in enumerate(self._chain):
            try:
                return await call(self._member(position))
            except _RECOVERABLE as exc:
                failures[provider_name] = exc
        raise self._no_backend(failures)

    def _eligible(self, request: TemplateRequest) -> list[tuple[int, str]]:
        """The chain members that map every option this request sets and return the format it asks for.

        Read from their classes, as the search and extract chains read theirs,
        so a member passed over is never built or called.

        Raises:
            UnsupportedParameterError: if no member maps an option the request
                sets, naming what each one lacked. Nothing has been run.
            UnsupportedFormatError: if every member that maps the options
                cannot return the page format asked for.
        """
        from web_router.registry import get_template_provider_class

        wanted = request.specified_parameters()
        eligible: list[tuple[int, str]] = []
        skipped: list[str] = []
        unmapped: frozenset[str] = frozenset()
        # The formats of the members that map every option: when one such
        # member exists and none returns the format, the format is the problem.
        returned_by_mapping: frozenset[ExtractFormat] | None = None
        for position, (provider_name, _) in enumerate(self._chain):
            try:
                member_cls = get_template_provider_class(provider_name)
            except ProviderNotInstalledError:
                eligible.append((position, provider_name))
                continue
            lacks = wanted - member_cls.supported_parameters
            if lacks:
                reasons = MSG_AUTO_UNMAPPED.format(names=_quoted(lacks))
                unmapped |= lacks
            elif request.format is not None and request.format not in member_cls.supported_formats:
                reasons = MSG_AUTO_UNFORMATTED.format(formats=_quoted(member_cls.supported_formats))
                returned_by_mapping = (returned_by_mapping or frozenset()) | member_cls.supported_formats
            else:
                eligible.append((position, provider_name))
                continue
            skipped.append(MSG_AUTO_TEMPLATE_SKIPPED.format(name=provider_name, reasons=reasons))

        if eligible or not skipped:
            return eligible
        detail = MSG_AUTO_TEMPLATE_NO_CAPABLE_BACKEND.format(skipped=", ".join(dict.fromkeys(skipped)))
        if returned_by_mapping is not None and request.format is not None:
            raise UnsupportedFormatError(self.name, request.format, returned_by_mapping, detail=detail)
        raise UnsupportedParameterError(self.name, unmapped or wanted, detail=detail)

    def _no_backend(self, failures: dict[str, WebRouterError]) -> WebRouterError:
        return _no_backend(
            self.name,
            failures,
            preferred=AUTO_TEMPLATE_PREFERRED,
            env_by_provider=TEMPLATE_ENV_BY_PROVIDER,
            no_credential=MSG_AUTO_TEMPLATE_NO_CREDENTIAL,
            exhausted=MSG_AUTO_TEMPLATE_EXHAUSTED,
        )

    def close(self) -> None:
        for member in self._members.values():
            member.close()

    async def aclose(self) -> None:
        for member in self._members.values():
            await member.aclose()
