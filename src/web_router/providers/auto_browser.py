"""``auto`` for ``open_browser()``: the browser vendors you hold a key for, then ``local``.

Kept apart from :mod:`web_router.providers.auto`, which re-exports it. A
session bills from the moment it opens, so the chain falls through only while
no session exists: once a member has opened one, the handle is that member's,
and nothing ``auto`` does can open a second.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any, ClassVar

from web_router.constants import (
    AUTO_BROWSER_KEYLESS,
    AUTO_BROWSER_PREFERRED,
    AUTO_BROWSER_STEALTH_LEVELS,
    AUTO_CHAIN_BROWSER,
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
    PROVIDER_AUTO,
)
from web_router.exceptions import MissingParameterError, ProviderError, UnsupportedParameterError, WebRouterError
from web_router.providers._chain import (
    _RECOVERABLE,
    _ChainSpec,
    _exhausted,
    _keyed_chain,
    _member_options,
    _members_and_chain,
    _nothing_started,
    _quoted,
    _valued,
)
from web_router.providers.base import AsyncBrowserHandle, BrowserHandle, BrowserProvider
from web_router.types import BROWSER_PARAMETERS, BrowserRequest, BrowserSession, StealthLevel


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

        if request.provider_options:
            raise UnsupportedParameterError(
                self.name, {"provider_options"}, detail=MSG_AUTO_BROWSER_NO_PROVIDER_OPTIONS
            )
        profile_options = {name for name in ("profile", "persist_profile") if getattr(request, name) is not None}
        if profile_options:
            raise UnsupportedParameterError(self.name, profile_options, detail=MSG_AUTO_BROWSER_NO_PROFILE)

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
