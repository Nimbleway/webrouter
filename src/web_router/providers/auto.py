"""The providers used when the caller named none, one per method.

Tries providers in order and returns the first answer: the ones you have a
credential for first, then the keyless tiers. A key in the environment says
which backend you want, so reaching for an anonymous rate-limited tier while
one sits configured would be perverse -- but with no key at all the router
still searches, rather than raising about a credential nobody mentioned.

The order is fixed rather than random. A router that picked a different backend
each run would return different result counts and content lengths for the same
query, and a bug reported against it could not be reproduced -- in a package
whose promise is that a query behaves the same whichever backend serves it, the
backend is the last thing that should be a coin flip. The chain still delivers
what a random pick was reaching for: when one anonymous tier is rate-limited,
the next one answers.

``SearchResponse.provider`` and ``ExtractResponse.provider`` name the backend
that actually served the call, never ``"auto"``, so a caller can always tell who
answered.

Every chain routes per request. ``auto`` declares what some member maps -- for
search every option Nimble does, for extract every format -- and before trying a
member reads its class, which costs no credential and sends nothing: a member
that does not map an option the request set, or cannot return the format it
asks for, is passed over for that request. When none is left the call raises,
naming why each one was, rather than running under weaker terms than asked.

An answer with nothing in it falls through too: a search with no results and
no answer, or an extract whose every document is blank, moves on to the next
member as a failure would. When every member answers empty, the first empty
answer is returned rather than an error, since an empty result is still an
answer. ``fallback_on_empty=False`` returns the first answer, empty or not.

``chain`` replaces the default order, as a list for every method's ``auto`` or
keyed by method for one alone -- ``provider_config={"auto": {"chain":
{"search": ["perplexity", "nimble"]}}}`` on the router.

Research is the exception to the fallback: there is no keyless tier after the
credentialed ones, because every research backend bills for the run. It is also
the one method where the choice outlives the call -- a run is a job, so the
member that accepted it is the one every later poll goes back to. An Extract
Template run has no keyless tier either -- it fetches and parses on the
backend's account -- but it is a call, not a job, so a member that refused
falls through to the next as a search does.

A browser session outlives the call too, and bills while it does, so ``auto``
falls through only while no session exists: once a member has opened one, the
handle is that member's, and nothing ``auto`` does can open a second. Browse
follows the same rule for a task, and like research keeps each task with the
member that accepted it.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any, ClassVar

from web_router.constants import (
    AUTO_APPENDED,
    AUTO_BROWSE_JOB_SEPARATOR,
    AUTO_BROWSE_PREFERRED,
    AUTO_BROWSE_SUPPORTED_PARAMETERS,
    AUTO_BROWSER_KEYLESS,
    AUTO_BROWSER_OWN_OPTIONS,
    AUTO_BROWSER_PREFERRED,
    AUTO_BROWSER_PROFILE_OPTIONS,
    AUTO_BROWSER_STEALTH_LEVELS,
    AUTO_CHAIN_BROWSE,
    AUTO_CHAIN_BROWSER,
    AUTO_CHAIN_EXTRACT,
    AUTO_CHAIN_RESEARCH,
    AUTO_CHAIN_SEARCH,
    AUTO_EXTRACT_APPENDED,
    AUTO_EXTRACT_DEFAULT_FORMAT,
    AUTO_EXTRACT_FORMATS,
    AUTO_EXTRACT_KEYLESS,
    AUTO_EXTRACT_PREFERRED,
    AUTO_KEYLESS,
    AUTO_PREFERRED,
    AUTO_RESEARCH_JOB_SEPARATOR,
    AUTO_RESEARCH_PREFERRED,
    AUTO_RESEARCH_SUPPORTED_PARAMETERS,
    AUTO_SUPPORTED_PARAMETERS,
    BROWSE_ENV_BY_PROVIDER,
    MSG_AUTO_BROWSE_EXHAUSTED,
    MSG_AUTO_BROWSE_MISSING,
    MSG_AUTO_BROWSE_NO_CREDENTIAL,
    MSG_AUTO_BROWSE_NO_MEMBER,
    MSG_AUTO_BROWSE_REFUSES,
    MSG_AUTO_BROWSE_SKIPPED,
    MSG_AUTO_BROWSE_UNKNOWN_JOB,
    MSG_AUTO_BROWSE_UNMAPPED,
    MSG_AUTO_BROWSER_EXHAUSTED,
    MSG_AUTO_BROWSER_NEEDS,
    MSG_AUTO_BROWSER_NO_ELIGIBLE,
    MSG_AUTO_BROWSER_NO_ELIGIBLE_MISSING,
    MSG_AUTO_BROWSER_NO_PROFILE,
    MSG_AUTO_BROWSER_NO_PROVIDER_OPTIONS,
    MSG_AUTO_BROWSER_OWNS_NOTHING,
    MSG_AUTO_BROWSER_SKIPPED,
    MSG_AUTO_BROWSER_UNMAPPED,
    MSG_AUTO_BROWSER_UNSUPPORTED,
    MSG_AUTO_EXTRACT_NO_MEMBER,
    MSG_AUTO_RESEARCH_NEEDS,
    MSG_AUTO_RESEARCH_NO_CAPABLE_BACKEND,
    MSG_AUTO_RESEARCH_REFUSES,
    MSG_AUTO_RESEARCH_SKIPPED,
    MSG_AUTO_RESEARCH_UNKNOWN_JOB,
    MSG_AUTO_SEARCH_NO_MEMBER,
    MSG_AUTO_SKIPPED,
    MSG_AUTO_UNFORMATTED,
    MSG_AUTO_UNMAPPED,
    PROVIDER_AUTO,
)
from web_router.exceptions import (
    DeadlineExceededError,
    MissingParameterError,
    ProviderError,
    ProviderNotInstalledError,
    UnsupportedFormatError,
    UnsupportedParameterError,
    WebRouterError,
)
from web_router.providers._chain import (
    _RECOVERABLE,
    _ChainSpec,
    _empty_extract,
    _empty_search,
    _exhausted,
    _held_or_late,
    _held_or_raise,
    _keyed_chain,
    _keyless_after,
    _late_answer,
    _member_options,
    _members_and_chain,
    _no_backend,
    _nothing_started,
    _quoted,
    _valued,
)
from web_router.providers.base import (
    AsyncBrowserHandle,
    BrowseJob,
    BrowseProvider,
    BrowserHandle,
    BrowserProvider,
    ExtractProvider,
    ResearchJob,
    ResearchProvider,
    SearchProvider,
)
from web_router.types import (
    BROWSER_PARAMETERS,
    EXTRACT_PARAMETERS,
    BrowseProgress,
    BrowseRequest,
    BrowserRequest,
    BrowserSession,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
    ResearchProgress,
    ResearchRequest,
    SearchRequest,
    SearchResponse,
    StealthLevel,
)


class AutoProvider(SearchProvider):
    """Searches through the first backend that answers, credentialed ones first.

    Declares every option some member maps, and sends each request only to the
    members that map every option it sets -- read from their classes, so one
    passed over is never built or called. With none left it raises before
    searching anything, naming what each member lacked.
    """

    name: ClassVar[str] = PROVIDER_AUTO
    supported_parameters: ClassVar[frozenset[str]] = AUTO_SUPPORTED_PARAMETERS

    def __init__(
        self,
        *,
        chain: _ChainSpec | None = None,
        members_config: Mapping[str, Mapping[str, Any]] | None = None,
        fallback_on_empty: bool = True,
        **kwargs: Any,
    ) -> None:
        """
        Args:
            chain: Backends to try, in order, each a name or ``(name, kwargs)``
                -- or a mapping from method to such a list, of which this reads
                its ``"search"`` entry. Defaults to :meth:`default_chain` for
                the current environment, which a chain given replaces whole,
                keyless tail included.
            members_config: The router's ``provider_config``, by backend: each
                member is built with its entry, the chain's own options laid
                over it, and a backend given an ``api_key`` (or Nimble an
                ``async_client``) there counts as held as well as one whose
                key is in the environment.
            fallback_on_empty: Whether a member that answers with no results
                and no answer is passed over for the next, as a failure is.
            **kwargs: Ignored. Accepted so a router configured for several
                providers can hand this one its config without special-casing.
        """
        super().__init__(**kwargs)
        self._members_config, self._chain = _members_and_chain(
            chain, members_config, AUTO_CHAIN_SEARCH, self.default_chain
        )
        self.fallback_on_empty = fallback_on_empty
        # Keyed by position, not by name: the same provider can appear twice on
        # different terms -- Tavily keyed, then Tavily keyless.
        self._members: dict[int, SearchProvider] = {}

    @staticmethod
    def default_chain(in_code: Collection[str] = ()) -> tuple[tuple[str, dict[str, Any]], ...]:
        """Providers you have a credential for, then the keyless ones, then members added since, with a credential."""
        from web_router.registry import configured_providers

        configured = set(configured_providers()) | set(in_code)
        keyed = _keyed_chain(AUTO_PREFERRED, configured)
        appended = _keyed_chain(AUTO_APPENDED, configured)
        return keyed + _keyless_after(keyed + appended, AUTO_KEYLESS) + appended

    @property
    def members(self) -> tuple[str, ...]:
        """The backends this instance would try, in order: its ``chain``, or the default it resolved at construction."""
        return tuple(provider_name for provider_name, _ in self._chain)

    def _member(self, position: int) -> SearchProvider:
        """Build and cache one member. Imported here so the chain stays lazy."""
        from web_router.registry import create_provider

        if position not in self._members:
            provider_name, options = self._chain[position]
            self._members[position] = create_provider(
                provider_name, **_member_options(self._members_config, provider_name, options)
            )
        return self._members[position]

    def _search(self, request: SearchRequest) -> SearchResponse:
        failures: dict[str, WebRouterError] = {}
        held: SearchResponse | None = None
        for position, provider_name in self._eligible(request):
            if (late := _late_answer(held, self.name, failures)) is not None:
                return late
            try:
                response = self._member(position).search(request)
            except DeadlineExceededError as exc:
                return _held_or_late(held, self.name, failures, exc, provider_name)
            except _RECOVERABLE as exc:
                failures[provider_name] = exc
                continue
            if not (self.fallback_on_empty and _empty_search(response)):
                return response
            # Not `held or response`: an empty response is falsy, having no length.
            held = response if held is None else held
        return _held_or_raise(held, self.name, failures)

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        failures: dict[str, WebRouterError] = {}
        held: SearchResponse | None = None
        for position, provider_name in self._eligible(request):
            if (late := _late_answer(held, self.name, failures)) is not None:
                return late
            try:
                response = await self._member(position).asearch(request)
            except DeadlineExceededError as exc:
                return _held_or_late(held, self.name, failures, exc, provider_name)
            except _RECOVERABLE as exc:
                failures[provider_name] = exc
                continue
            if not (self.fallback_on_empty and _empty_search(response)):
                return response
            held = response if held is None else held
        return _held_or_raise(held, self.name, failures)

    def _eligible(self, request: SearchRequest) -> list[tuple[int, str]]:
        """The chain members that map every option this request sets, in order.

        A member whose extra is not installed cannot be read, and is kept:
        trying it fails as a missing extra always has, and the chain moves on.

        Raises:
            UnsupportedParameterError: if no member maps everything set, naming
                what each one lacked. Nothing has been searched.
        """
        from web_router.registry import get_provider_class

        wanted = request.specified_parameters()
        eligible: list[tuple[int, str]] = []
        skipped: list[str] = []
        unmapped: frozenset[str] = frozenset()
        widest: frozenset[str] = frozenset()
        for position, (provider_name, _) in enumerate(self._chain):
            try:
                supported = get_provider_class(provider_name).supported_parameters
            except ProviderNotInstalledError:
                eligible.append((position, provider_name))
                continue
            widest |= supported
            lacks = wanted - supported
            if not lacks:
                eligible.append((position, provider_name))
                continue
            skipped.append(
                MSG_AUTO_SKIPPED.format(name=provider_name, reasons=MSG_AUTO_UNMAPPED.format(names=_quoted(lacks)))
            )
            unmapped |= lacks

        if eligible or not skipped:
            return eligible
        detail = MSG_AUTO_SEARCH_NO_MEMBER.format(skipped=", ".join(dict.fromkeys(skipped)))
        # As for browse: name what no member maps at all when there is such a
        # thing, since that is the option to drop.
        raise UnsupportedParameterError(self.name, (wanted - widest) or unmapped, detail=detail)

    def close(self) -> None:
        for member in self._members.values():
            member.close()

    async def aclose(self) -> None:
        for member in self._members.values():
            await member.aclose()


class AutoExtractProvider(ExtractProvider):
    """Extracts through the first backend that answers, credentialed ones first.

    Declares every format some member returns, and sends each request only to
    the members that return the one it asks for -- the default included, which
    is pinned before routing -- so a format is never relabelled. With none left
    it raises before fetching anything, naming what each member returns.
    """

    name: ClassVar[str] = PROVIDER_AUTO
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_PARAMETERS
    supported_formats: ClassVar[frozenset[ExtractFormat]] = AUTO_EXTRACT_FORMATS
    default_format: ClassVar[ExtractFormat] = AUTO_EXTRACT_DEFAULT_FORMAT
    # The members each batch as their own API allows, so there is nothing to
    # split up here: one delegated call carries the whole request.
    batches_urls: ClassVar[bool] = True

    def __init__(
        self,
        *,
        chain: _ChainSpec | None = None,
        members_config: Mapping[str, Mapping[str, Any]] | None = None,
        fallback_on_empty: bool = True,
        **kwargs: Any,
    ) -> None:
        """
        Args:
            chain: Backends to try, in order, as on :class:`AutoProvider`; keyed
                by method, this reads the ``"extract"`` entry.
            fallback_on_empty: Whether a member whose every document is blank
                is passed over for the next, as a failure is.
            **kwargs: Ignored, as on :class:`AutoProvider`.
        """
        super().__init__(**kwargs)
        self._members_config, self._chain = _members_and_chain(
            chain, members_config, AUTO_CHAIN_EXTRACT, self.default_chain
        )
        self.fallback_on_empty = fallback_on_empty
        self._members: dict[int, ExtractProvider] = {}

    @staticmethod
    def default_chain(in_code: Collection[str] = ()) -> tuple[tuple[str, dict[str, Any]], ...]:
        """Backends you have credentials for, then the keyless ones, then members added since, with credentials."""
        from web_router.registry import configured_extract_providers

        configured = set(configured_extract_providers()) | set(in_code)
        keyed = _keyed_chain(AUTO_EXTRACT_PREFERRED, configured)
        appended = _keyed_chain(AUTO_EXTRACT_APPENDED, configured)
        return keyed + _keyless_after(keyed + appended, AUTO_EXTRACT_KEYLESS) + appended

    @property
    def members(self) -> tuple[str, ...]:
        """The backends this instance would try, in order, as on :class:`AutoProvider`."""
        return tuple(provider_name for provider_name, _ in self._chain)

    def _member(self, position: int) -> ExtractProvider:
        """Build and cache one member. Imported here so the chain stays lazy."""
        from web_router.registry import create_extract_provider

        if position not in self._members:
            provider_name, options = self._chain[position]
            self._members[position] = create_extract_provider(
                provider_name, **_member_options(self._members_config, provider_name, options)
            )
        return self._members[position]

    def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        delegated = self._delegated(request, urls)
        failures: dict[str, WebRouterError] = {}
        held: ExtractResponse | None = None
        for position, provider_name in self._eligible(self.format_of(request)):
            if (late := _late_answer(held, self.name, failures)) is not None:
                return late
            try:
                response = self._member(position).extract(delegated)
            except DeadlineExceededError as exc:
                return _held_or_late(held, self.name, failures, exc, provider_name)
            except _RECOVERABLE as exc:
                failures[provider_name] = exc
                continue
            if not (self.fallback_on_empty and _empty_extract(response)):
                return response
            held = response if held is None else held
        return _held_or_raise(held, self.name, failures)

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        delegated = self._delegated(request, urls)
        failures: dict[str, WebRouterError] = {}
        held: ExtractResponse | None = None
        for position, provider_name in self._eligible(self.format_of(request)):
            if (late := _late_answer(held, self.name, failures)) is not None:
                return late
            try:
                response = await self._member(position).aextract(delegated)
            except DeadlineExceededError as exc:
                return _held_or_late(held, self.name, failures, exc, provider_name)
            except _RECOVERABLE as exc:
                failures[provider_name] = exc
                continue
            if not (self.fallback_on_empty and _empty_extract(response)):
                return response
            held = response if held is None else held
        return _held_or_raise(held, self.name, failures)

    def _eligible(self, wanted: ExtractFormat) -> list[tuple[int, str]]:
        """The chain members that return ``wanted``, in order, read from their classes as search's are.

        Raises:
            UnsupportedFormatError: if no member returns it, naming what each one
                returns instead. Nothing has been fetched.
        """
        from web_router.registry import get_extract_provider_class

        eligible: list[tuple[int, str]] = []
        skipped: list[str] = []
        returned: frozenset[ExtractFormat] = frozenset()
        for position, (provider_name, _) in enumerate(self._chain):
            try:
                formats = get_extract_provider_class(provider_name).supported_formats
            except ProviderNotInstalledError:
                eligible.append((position, provider_name))
                continue
            returned |= formats
            if wanted in formats:
                eligible.append((position, provider_name))
                continue
            skipped.append(
                MSG_AUTO_SKIPPED.format(
                    name=provider_name, reasons=MSG_AUTO_UNFORMATTED.format(formats=_quoted(formats))
                )
            )

        if eligible or not skipped:
            return eligible
        detail = MSG_AUTO_EXTRACT_NO_MEMBER.format(skipped=", ".join(dict.fromkeys(skipped)))
        raise UnsupportedFormatError(self.name, wanted, returned, detail=detail)

    def _delegated(self, request: ExtractRequest, urls: list[str]) -> ExtractRequest:
        """The request to hand a member, with the format named explicitly.

        The members disagree about their own defaults -- Nimble returns HTML,
        Tavily markdown -- so letting one fall through would make the shape of
        the content depend on which backend happened to answer.
        """
        return request.model_copy(update={"format": self.format_of(request), "urls": urls})

    def close(self) -> None:
        for member in self._members.values():
            member.close()

    async def aclose(self) -> None:
        for member in self._members.values():
            await member.aclose()


class AutoResearchProvider(ResearchProvider):
    """Researches through the first backend that answers. Every member needs a credential."""

    name: ClassVar[str] = PROVIDER_AUTO
    supported_parameters: ClassVar[frozenset[str]] = AUTO_RESEARCH_SUPPORTED_PARAMETERS

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
                by method, this reads the ``"research"`` entry.
            **kwargs: Ignored, as on :class:`AutoProvider`.
        """
        super().__init__(**kwargs)
        self._members_config, self._chain = _members_and_chain(
            chain, members_config, AUTO_CHAIN_RESEARCH, self.default_chain
        )
        self._members: dict[int, ResearchProvider] = {}
        # A run belongs to the backend that accepted it: its id means nothing to
        # the others, so the member that answered is kept and every later poll
        # goes back to it rather than re-entering the chain.
        self._jobs: dict[str, ResearchJob] = {}

    @staticmethod
    def default_chain(in_code: Collection[str] = ()) -> tuple[tuple[str, dict[str, Any]], ...]:
        """The backends you have credentials for. Nothing follows them: they all bill."""
        from web_router.registry import configured_research_providers

        return _keyed_chain(AUTO_RESEARCH_PREFERRED, set(configured_research_providers()) | set(in_code))

    def _member(self, position: int) -> ResearchProvider:
        """Build and cache one member. Imported here so the chain stays lazy."""
        from web_router.registry import create_research_provider

        if position not in self._members:
            provider_name, options = self._chain[position]
            self._members[position] = create_research_provider(
                provider_name, **_member_options(self._members_config, provider_name, options)
            )
        return self._members[position]

    def _start(self, request: ResearchRequest) -> ResearchProgress:
        eligible = self._eligible(request)
        failures: dict[str, WebRouterError] = {}
        for position, provider_name in eligible:
            try:
                job = self._member(position).submit(request)
            except _RECOVERABLE as exc:
                # A 5xx, a read timeout or an unreadable answer may come after
                # the backend created the run, so only a failure that proves
                # nothing started may try the next member -- a run is billed
                # from the moment it was accepted.
                if not _nothing_started(exc):
                    raise
                failures[provider_name] = exc
                continue
            # Registered from the start progress, with no poll first: a poll
            # that failed here would leave a billed run with no handle to it.
            return self._took(provider_name, job)
        raise _no_backend(self.name, failures)

    async def _astart(self, request: ResearchRequest) -> ResearchProgress:
        eligible = self._eligible(request)
        failures: dict[str, WebRouterError] = {}
        for position, provider_name in eligible:
            try:
                job = await self._member(position).asubmit(request)
            except _RECOVERABLE as exc:
                if not _nothing_started(exc):
                    raise
                failures[provider_name] = exc
                continue
            return self._took(provider_name, job)
        raise _no_backend(self.name, failures)

    def _eligible(self, request: ResearchRequest) -> list[tuple[int, str]]:
        """The chain members that map everything this request asks for.

        `auto` offers a wider surface than some of its members: Nimble and
        Parallel return structured output and Perplexity does not, and dropping
        `output_schema` from the shared surface to match the weakest member
        would cost the other two a capability they have. So a member that
        cannot honour *this* request is passed over instead -- checked against
        the class, which costs no credential and starts no billed run.

        Raises:
            UnsupportedParameterError: if no member maps the options set, which
                is the honest answer rather than a routing failure: the request
                cannot be served by anything configured, and the message says
                which backend would have taken it without that option.
        """
        from web_router.registry import get_research_provider_class

        wanted = request.specified_parameters()
        eligible: list[tuple[int, str]] = []
        skipped: list[str] = []
        missing: frozenset[str] = frozenset()
        refused: dict[str, Any] = {}
        for position, (provider_name, _) in enumerate(self._chain):
            member_cls = get_research_provider_class(provider_name)
            supported = member_cls.supported_parameters
            if not wanted <= supported:
                skipped.append(
                    MSG_AUTO_RESEARCH_SKIPPED.format(
                        name=provider_name, supported=", ".join(sorted(supported)) or "nothing"
                    )
                )
            elif needs := member_cls.missing_parameters(request):
                skipped.append(MSG_AUTO_RESEARCH_NEEDS.format(name=provider_name, names=_quoted(needs)))
                missing |= needs
            elif refuses := member_cls.refused_values(request):
                skipped.append(MSG_AUTO_RESEARCH_REFUSES.format(name=provider_name, values=_valued(refuses)))
                refused.update(refuses)
            else:
                eligible.append((position, provider_name))

        if not eligible and missing:
            raise MissingParameterError(self.name, missing, detail=", ".join(skipped))
        if not eligible and refused:
            raise UnsupportedParameterError(self.name, refused, detail=", ".join(skipped), values=refused)
        if not eligible and skipped:
            # Every option may be mapped by some member and yet no single member
            # map them all; then the whole set is what nothing could take.
            detail = MSG_AUTO_RESEARCH_NO_CAPABLE_BACKEND.format(skipped=", ".join(skipped))
            raise UnsupportedParameterError(self.name, (wanted - self._widest()) or wanted, detail=detail)
        return eligible

    def _widest(self) -> frozenset[str]:
        """Everything some member of the chain maps, for naming what nothing could."""
        from web_router.registry import get_research_provider_class

        supported: frozenset[str] = frozenset()
        for provider_name, _ in self._chain:
            supported |= get_research_provider_class(provider_name).supported_parameters
        return supported

    def _took(self, provider_name: str, job: ResearchJob) -> ResearchProgress:
        """Remember which member owns this run, and hand back a handle that says so.

        Keyed by the member's name beside its own id rather than by the id
        alone: run ids are issued independently by each backend and mean nothing
        to the others, so two that collided would otherwise be one entry, and
        polling the first would answer about the second.
        """
        handle = f"{provider_name}{AUTO_RESEARCH_JOB_SEPARATOR}{job.job_id}"
        self._jobs[handle] = job
        return job.progress.model_copy(update={"job_id": handle})

    def _fetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
        return self._as_handle(job_id, self._job(job_id).status())

    async def _afetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
        return self._as_handle(job_id, await self._job(job_id).astatus())

    @staticmethod
    def _as_handle(job_id: str, progress: ResearchProgress) -> ResearchProgress:
        """Re-label a member's progress with the handle the caller holds.

        The member answers with its own run id. Handing that back would make the
        next poll ask about an id this class never stored, so a job would work
        exactly once.
        """
        return progress.model_copy(update={"job_id": job_id})

    def _cancel(self, job_id: str) -> bool:
        return self._job(job_id).cancel()

    async def _acancel(self, job_id: str) -> bool:
        return await self._job(job_id).acancel()

    def _job(self, job_id: str) -> ResearchJob:
        """The member's own job for this id, which is the only thing that can answer for it."""
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise ProviderError(self.name, MSG_AUTO_RESEARCH_UNKNOWN_JOB.format(job_id=job_id)) from exc

    def close(self) -> None:
        for member in self._members.values():
            member.close()

    async def aclose(self) -> None:
        for member in self._members.values():
            await member.aclose()


class AutoBrowserProvider(BrowserProvider):
    """Opens a session through the first backend that will, credentialed ones first, then ``local``.

    Each member is checked against the request on its class before anything is
    opened, as ``auto`` research does: ``stealth="low"`` passes over ``local``,
    which has none, ``idle_timeout`` passes over every backend without an idle
    limit, and a member is passed over too for a stealth level it lacks, a
    country it has no proxy in, or an option it serves only beside one the
    request left unset. The :data:`~web_router.constants.BROWSER_TIMEOUT` default does
    not count, because it is ours rather than the caller's: counted, it would
    pass over every backend with no lifetime limit on behalf of an option
    nobody set. The member still applies it for itself, exactly as it would if
    the caller had named it.

    ``provider_options`` is refused rather than passed on. It is one backend's
    own fields, untranslated, so it cannot mean the same thing to whichever
    member happens to answer -- to ``local`` it is Chrome command-line
    switches -- and a caller who wrote it for one backend should name it.
    ``profile`` and ``persist_profile`` are refused for the same reason: a
    profile is saved on one backend, under that backend's own id or name.

    The handle returned is the member's own: it reports that member as its
    provider, and its release and status go straight to it.
    """

    name: ClassVar[str] = PROVIDER_AUTO
    # Everything some backend could honour; whether one in *this* chain can is
    # decided per request, by eligibility.
    supported_parameters: ClassVar[frozenset[str]] = BROWSER_PARAMETERS
    stealth_levels: ClassVar[frozenset[StealthLevel]] = AUTO_BROWSER_STEALTH_LEVELS

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
                by method, this reads the ``"browser"`` entry.
            **kwargs: Ignored, as on :class:`AutoProvider`.
        """
        super().__init__(**kwargs)
        self._members_config, self._chain = _members_and_chain(
            chain, members_config, AUTO_CHAIN_BROWSER, self.default_chain
        )
        self._members: dict[int, BrowserProvider] = {}

    @staticmethod
    def default_chain(in_code: Collection[str] = ()) -> tuple[tuple[str, dict[str, Any]], ...]:
        """The backends you hold credentials for, in preference order, then ``local``."""
        from web_router.registry import configured_browser_providers

        return (
            _keyed_chain(AUTO_BROWSER_PREFERRED, set(configured_browser_providers()) | set(in_code))
            + AUTO_BROWSER_KEYLESS
        )

    @property
    def members(self) -> tuple[str, ...]:
        """The backends this instance would try, in order: its ``chain``, or the default it resolved at construction."""
        return tuple(provider_name for provider_name, _ in self._chain)

    def _member(self, position: int) -> BrowserProvider:
        """Build and cache one member. Imported here so the chain stays lazy."""
        from web_router.registry import create_browser_provider

        if position not in self._members:
            provider_name, options = self._chain[position]
            self._members[position] = create_browser_provider(
                provider_name, **_member_options(self._members_config, provider_name, options)
            )
        return self._members[position]

    def _opened(self, request: BrowserRequest) -> BrowserHandle:
        failures: dict[str, WebRouterError] = {}
        for position, provider_name in self._eligible(request):
            try:
                # Returned as it is: the first session opened is the only one.
                return self._member(position).open(request)
            except _RECOVERABLE as exc:
                if not _nothing_started(exc):
                    raise
                failures[provider_name] = exc
        raise _exhausted(self.name, failures, MSG_AUTO_BROWSER_EXHAUSTED)

    async def _aopened(self, request: BrowserRequest) -> AsyncBrowserHandle:
        failures: dict[str, WebRouterError] = {}
        for position, provider_name in self._eligible(request):
            try:
                return await self._member(position).aopen(request)
            except _RECOVERABLE as exc:
                if not _nothing_started(exc):
                    raise
                failures[provider_name] = exc
        raise _exhausted(self.name, failures, MSG_AUTO_BROWSER_EXHAUSTED)

    @classmethod
    def refused_options(cls, request: BrowserRequest) -> frozenset[str]:
        """The options this request sets that ``auto`` refuses whatever its members map: a backend's own, a profile.

        On the class, as :meth:`refused_values` is, so a preflight can report
        them without a credential, through the same check :meth:`_eligible` makes.
        """
        refused = {name for name in AUTO_BROWSER_PROFILE_OPTIONS if getattr(request, name) is not None}
        if request.provider_options:
            refused.add(AUTO_BROWSER_OWN_OPTIONS)
        return frozenset(refused)

    def _eligible(self, request: BrowserRequest) -> list[tuple[int, str]]:
        """The chain members that can serve every option the caller set, as it was set, in order.

        A member is passed over for an option it does not map, a value of one it
        cannot serve, or an option it serves only beside one the request left
        unset -- each read from its class, through the checks its own
        ``validate`` makes, so nothing is spent finding out.

        Raises:
            MissingParameterError: if no member qualifies and one of them would
                but for a partner option left unset -- setting it is the fix.
            UnsupportedParameterError: for ``provider_options`` and a profile,
                and when no member qualifies otherwise. Either message names why each
                member was passed over.
        """
        from web_router.registry import get_browser_provider_class

        outright = self.refused_options(request)
        if AUTO_BROWSER_OWN_OPTIONS in outright:
            raise UnsupportedParameterError(
                self.name, {AUTO_BROWSER_OWN_OPTIONS}, detail=MSG_AUTO_BROWSER_NO_PROVIDER_OPTIONS
            )
        if outright:
            raise UnsupportedParameterError(self.name, outright, detail=MSG_AUTO_BROWSER_NO_PROFILE)

        wanted = request.specified_parameters()
        eligible: list[tuple[int, str]] = []
        skipped: list[str] = []
        unmapped: set[str] = set()
        refused: dict[str, Any] = {}
        missing: set[str] = set()
        for position, (provider_name, _) in enumerate(self._chain):
            member_cls = get_browser_provider_class(provider_name)
            lacks = wanted - member_cls.supported_parameters
            refuses = member_cls.refused_values(request)
            needs = member_cls.unmet_requirements(request)
            if not lacks and not refuses and not needs:
                eligible.append((position, provider_name))
                continue
            partners = frozenset().union(*needs.values())
            reasons = [MSG_AUTO_BROWSER_UNMAPPED.format(names=_quoted(lacks))] if lacks else []
            if refuses:
                reasons.append(MSG_AUTO_BROWSER_UNSUPPORTED.format(values=_valued(refuses)))
            if needs:
                beside = {option: getattr(request, option) for option in needs}
                reasons.append(MSG_AUTO_BROWSER_NEEDS.format(names=_quoted(partners), values=_valued(beside)))
            skipped.append(MSG_AUTO_BROWSER_SKIPPED.format(name=provider_name, reasons=" and ".join(reasons)))
            unmapped |= lacks
            refused.update(refuses)
            if not lacks and not refuses:
                missing |= partners

        if eligible or not skipped:
            return eligible
        if missing:
            raise MissingParameterError(
                self.name, missing, detail=MSG_AUTO_BROWSER_NO_ELIGIBLE_MISSING.format(skipped="; ".join(skipped))
            )
        raise UnsupportedParameterError(
            self.name,
            unmapped | set(refused),
            values=refused,
            detail=MSG_AUTO_BROWSER_NO_ELIGIBLE.format(skipped="; ".join(skipped)),
        )

    def _open(self, request: BrowserRequest) -> BrowserSession:
        # Unreachable: open() goes through _opened, which returns a member's
        # handle, and a session unwrapped from it here would have no owner.
        raise NotImplementedError  # pragma: no cover

    async def _aopen(self, request: BrowserRequest) -> BrowserSession:
        raise NotImplementedError  # pragma: no cover

    def _release(self, session_id: str) -> None:
        # Reached only by calling release() on this provider directly, since no
        # handle it returned points back here.
        raise ProviderError(self.name, MSG_AUTO_BROWSER_OWNS_NOTHING.format(session_id=session_id))

    async def _arelease(self, session_id: str) -> None:
        self._release(session_id)

    def close(self) -> None:
        for member in self._members.values():
            member.close()

    async def aclose(self) -> None:
        for member in self._members.values():
            await member.aclose()


class AutoBrowseProvider(BrowseProvider):
    """Runs a browse task through the first backend that takes it. Every member needs a credential.

    A member is checked against the request before it is tried -- an option it
    does not map, ``start_url`` on a backend that refuses one, a parameter it
    cannot start without -- on the class, which costs no credential and starts
    no billed task. Among the members that pass, only a failure that proves no
    task was created moves on to the next; anything else raises, and once one
    has accepted the task nothing else is started.
    """

    name: ClassVar[str] = PROVIDER_AUTO
    supported_parameters: ClassVar[frozenset[str]] = AUTO_BROWSE_SUPPORTED_PARAMETERS

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
                by method, this reads the ``"browse"`` entry.
            **kwargs: Ignored, as on :class:`AutoProvider`.
        """
        super().__init__(**kwargs)
        self._members_config, self._chain = _members_and_chain(
            chain, members_config, AUTO_CHAIN_BROWSE, self.default_chain
        )
        self._members: dict[int, BrowseProvider] = {}
        # As for research: a task id means nothing to a backend that did not
        # issue it, so every later poll goes back to the member that took it.
        self._jobs: dict[str, BrowseJob] = {}

    @staticmethod
    def default_chain(in_code: Collection[str] = ()) -> tuple[tuple[str, dict[str, Any]], ...]:
        """The backends you have credentials for. Nothing follows them: they all bill."""
        from web_router.registry import configured_browse_providers

        return _keyed_chain(AUTO_BROWSE_PREFERRED, set(configured_browse_providers()) | set(in_code))

    def _member(self, position: int) -> BrowseProvider:
        """Build and cache one member. Imported here so the chain stays lazy."""
        from web_router.registry import create_browse_provider

        if position not in self._members:
            provider_name, options = self._chain[position]
            self._members[position] = create_browse_provider(
                provider_name, **_member_options(self._members_config, provider_name, options)
            )
        return self._members[position]

    def _start(self, request: BrowseRequest) -> BrowseProgress:
        failures: dict[str, WebRouterError] = {}
        for position, provider_name in self._eligible(request):
            try:
                job = self._member(position).submit(request)
            except _RECOVERABLE as exc:
                # A 5xx, a read timeout or an unreadable answer may come after
                # the vendor created the task, so only a failure that proves
                # nothing started may try the next member.
                if not _nothing_started(exc):
                    raise
                failures[provider_name] = exc
                continue
            # Registered from the start progress, with no poll first: a poll
            # that failed here would leave a billed task with no handle to it.
            return self._took(provider_name, job)
        raise self._no_backend(failures)

    async def _astart(self, request: BrowseRequest) -> BrowseProgress:
        failures: dict[str, WebRouterError] = {}
        for position, provider_name in self._eligible(request):
            try:
                job = await self._member(position).asubmit(request)
            except _RECOVERABLE as exc:
                if not _nothing_started(exc):
                    raise
                failures[provider_name] = exc
                continue
            return self._took(provider_name, job)
        raise self._no_backend(failures)

    def _no_backend(self, failures: dict[str, WebRouterError]) -> WebRouterError:
        return _no_backend(
            self.name,
            failures,
            preferred=AUTO_BROWSE_PREFERRED,
            env_by_provider=BROWSE_ENV_BY_PROVIDER,
            no_credential=MSG_AUTO_BROWSE_NO_CREDENTIAL,
            exhausted=MSG_AUTO_BROWSE_EXHAUSTED,
        )

    def _eligible(self, request: BrowseRequest) -> list[tuple[int, str]]:
        """The chain members that map everything this request sets and need nothing it left out.

        Raises:
            MissingParameterError: if no member qualifies and one of them maps
                everything asked for but cannot start without something unset
                -- setting it is the fix.
            UnsupportedParameterError: if no member qualifies otherwise.
            Either message lists every member and why it was passed over.
        """
        from web_router.registry import get_browse_provider_class

        wanted = request.specified_parameters()
        eligible: list[tuple[int, str]] = []
        skipped: list[str] = []
        unmapped: frozenset[str] = frozenset()
        missing: frozenset[str] = frozenset()
        refused: dict[str, Any] = {}
        widest: frozenset[str] = frozenset()
        for position, (provider_name, _) in enumerate(self._chain):
            member_cls = get_browse_provider_class(provider_name)
            widest |= member_cls.supported_parameters
            lacks = wanted - member_cls.supported_parameters
            needs = member_cls.missing_parameters(request)
            refuses = member_cls.refused_values(request)
            if not lacks and not needs and not refuses:
                eligible.append((position, provider_name))
                continue
            reasons = [MSG_AUTO_BROWSE_UNMAPPED.format(names=_quoted(lacks))] if lacks else []
            if needs:
                reasons.append(MSG_AUTO_BROWSE_MISSING.format(names=_quoted(needs)))
            if refuses:
                reasons.append(MSG_AUTO_BROWSE_REFUSES.format(values=_valued(refuses)))
            skipped.append(MSG_AUTO_BROWSE_SKIPPED.format(name=provider_name, reasons="; ".join(reasons)))
            unmapped |= lacks
            if not lacks:
                missing |= needs
                refused.update(refuses)

        if eligible or not skipped:
            return eligible
        detail = MSG_AUTO_BROWSE_NO_MEMBER.format(skipped=", ".join(skipped))
        if missing:
            raise MissingParameterError(self.name, missing, detail=detail)
        if refused:
            raise UnsupportedParameterError(self.name, refused, detail=detail, values=refused)
        # Name what no member maps at all when there is such a thing: that is the
        # option to drop. Otherwise each is mapped somewhere, just never together.
        raise UnsupportedParameterError(self.name, (wanted - widest) or unmapped, detail=detail)

    def _took(self, provider_name: str, job: BrowseJob) -> BrowseProgress:
        """Remember which member owns this task, under a handle that names it.

        The member's name is part of the handle because task ids are issued per
        backend, and two that collided would otherwise be one entry.
        """
        handle = f"{provider_name}{AUTO_BROWSE_JOB_SEPARATOR}{job.job_id}"
        self._jobs[handle] = job
        return job.progress.model_copy(update={"job_id": handle})

    def _fetch(self, request: BrowseRequest, job_id: str) -> BrowseProgress:
        return self._job(job_id).status().model_copy(update={"job_id": job_id})

    async def _afetch(self, request: BrowseRequest, job_id: str) -> BrowseProgress:
        return (await self._job(job_id).astatus()).model_copy(update={"job_id": job_id})

    def _cancel(self, job_id: str) -> bool:
        return self._job(job_id).cancel()

    async def _acancel(self, job_id: str) -> bool:
        return await self._job(job_id).acancel()

    def _job(self, job_id: str) -> BrowseJob:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise ProviderError(self.name, MSG_AUTO_BROWSE_UNKNOWN_JOB.format(job_id=job_id)) from exc

    def close(self) -> None:
        for member in self._members.values():
            member.close()

    async def aclose(self) -> None:
        for member in self._members.values():
            await member.aclose()


def resolve_default_chains(config: Mapping[str, Any]) -> dict[str, tuple[tuple[str, dict[str, Any]], ...]]:
    """Each method's chain as an ``auto`` built with ``config`` would resolve it, a configured ``chain`` included.

    ``config`` is what the router hands ``auto``: its ``chain`` and ``members_config``.
    """
    defaults = {
        AUTO_CHAIN_SEARCH: AutoProvider.default_chain,
        AUTO_CHAIN_EXTRACT: AutoExtractProvider.default_chain,
        AUTO_CHAIN_RESEARCH: AutoResearchProvider.default_chain,
        AUTO_CHAIN_BROWSE: AutoBrowseProvider.default_chain,
    }
    return {
        method: _members_and_chain(config.get("chain"), config.get("members_config"), method, default)[1]
        for method, default in defaults.items()
    }
