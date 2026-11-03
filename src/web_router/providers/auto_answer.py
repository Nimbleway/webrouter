"""``auto`` for ``answer()``: the answer backends you hold a key for, cheapest first.

Kept apart from :mod:`web_router.providers.auto`, which it follows. As for
search, each request goes only to the members that map every option it sets --
read from their classes, so one passed over is never built or called -- and a
member that fails, or answers with nothing, is passed over for the next. As
for research there is no keyless tier: every answer backend bills the call, so
with no key there is no chain, and the call says which variables would add one.

A member that refuses a value before sending -- Perplexity and You.com take one
domain list, never both -- is passed over too, since nothing was asked of it.
``AnswerResponse.provider`` names the member that answered, never ``"auto"``.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any, ClassVar

from web_router.constants import (
    ANSWER_ENV_BY_PROVIDER,
    AUTO_CHAIN_ANSWER,
    MSG_AUTO_SKIPPED,
    MSG_AUTO_UNMAPPED,
    PROVIDER_AUTO,
)
from web_router.constants_answer import (
    AUTO_ANSWER_PREFERRED,
    AUTO_ANSWER_SUPPORTED_PARAMETERS,
    MSG_AUTO_ANSWER_EXHAUSTED,
    MSG_AUTO_ANSWER_NO_MEMBER,
)
from web_router.exceptions import NoAnswerProviderError, UnsupportedParameterError, WebRouterError
from web_router.providers._chain import (
    _RECOVERABLE,
    _ChainSpec,
    _exhausted,
    _keyed_chain,
    _member_options,
    _members_and_chain,
    _quoted,
)
from web_router.providers.answer import AnswerProvider
from web_router.types_answer import AnswerRequest, AnswerResponse


class AutoAnswerProvider(AnswerProvider):
    """Answers through the first configured backend that answers, cheapest first."""

    name: ClassVar[str] = PROVIDER_AUTO
    supported_parameters: ClassVar[frozenset[str]] = AUTO_ANSWER_SUPPORTED_PARAMETERS

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
            chain: Backends to try, in order, as on
                :class:`~web_router.providers.auto.AutoProvider`; keyed by
                method, this reads the ``"answer"`` entry.
            members_config: The router's ``provider_config``, by backend, as on
                :class:`~web_router.providers.auto.AutoProvider`.
            fallback_on_empty: Whether a member whose answer is empty is passed
                over for the next, as a failure is.
            **kwargs: Ignored, as on :class:`~web_router.providers.auto.AutoProvider`.
        """
        super().__init__(**kwargs)
        self._members_config, self._chain = _members_and_chain(
            chain, members_config, AUTO_CHAIN_ANSWER, self.default_chain
        )
        self.fallback_on_empty = fallback_on_empty
        self._members: dict[int, AnswerProvider] = {}

    @staticmethod
    def default_chain(in_code: Collection[str] = ()) -> tuple[tuple[str, dict[str, Any]], ...]:
        """The answer backends you have a key for, cheapest first, then any others added since, by name.

        Nothing keyless follows them: every answer backend bills the call.
        """
        from web_router.registry_answer import available_answer_providers, configured_answer_providers

        # A key in code for a search-only backend is not an answer backend.
        answering = set(available_answer_providers()) - {PROVIDER_AUTO}
        configured = (set(configured_answer_providers()) | set(in_code)) & answering
        added = tuple(sorted(configured - set(AUTO_ANSWER_PREFERRED)))
        return _keyed_chain(AUTO_ANSWER_PREFERRED + added, configured)

    @property
    def members(self) -> tuple[str, ...]:
        """The backends this instance would try, in order."""
        return tuple(provider_name for provider_name, _ in self._chain)

    def _member(self, position: int) -> AnswerProvider:
        """Build and cache one member. Imported here so the chain stays lazy."""
        from web_router.registry_answer import create_answer_provider

        if position not in self._members:
            provider_name, options = self._chain[position]
            self._members[position] = create_answer_provider(
                provider_name, **_member_options(self._members_config, provider_name, options)
            )
        return self._members[position]

    def _answer(self, request: AnswerRequest) -> AnswerResponse:
        failures: dict[str, WebRouterError] = {}
        refused: list[UnsupportedParameterError] = []
        held: AnswerResponse | None = None
        for position, provider_name in self._eligible(request):
            try:
                response = self._member(position).answer(request)
            except UnsupportedParameterError as exc:
                refused.append(exc)
                continue
            except _RECOVERABLE as exc:
                failures[provider_name] = exc
                continue
            if not (self.fallback_on_empty and _empty_answer(response)):
                return response
            held = response if held is None else held
        return self._held_or_raise(held, failures, refused)

    async def _aanswer(self, request: AnswerRequest) -> AnswerResponse:
        failures: dict[str, WebRouterError] = {}
        refused: list[UnsupportedParameterError] = []
        held: AnswerResponse | None = None
        for position, provider_name in self._eligible(request):
            try:
                response = await self._member(position).aanswer(request)
            except UnsupportedParameterError as exc:
                refused.append(exc)
                continue
            except _RECOVERABLE as exc:
                failures[provider_name] = exc
                continue
            if not (self.fallback_on_empty and _empty_answer(response)):
                return response
            held = response if held is None else held
        return self._held_or_raise(held, failures, refused)

    def _held_or_raise(
        self, held: AnswerResponse | None, failures: dict[str, WebRouterError], refused: list[UnsupportedParameterError]
    ) -> AnswerResponse:
        """The first empty answer when one came back; else every failure; else the refusal when all refused."""
        if held is not None:
            return held
        if failures:
            raise _exhausted(self.name, failures, MSG_AUTO_ANSWER_EXHAUSTED)
        raise refused[0]

    def require_members(self) -> None:
        """Raise :class:`NoAnswerProviderError`, naming the variables to set, when the chain is empty."""
        from web_router.registry_answer import available_answer_providers

        if not self._chain:
            env_vars = {var for names in ANSWER_ENV_BY_PROVIDER.values() for var in names}
            raise NoAnswerProviderError(self.name, available_answer_providers(), [], env_vars)

    def _eligible(self, request: AnswerRequest) -> list[tuple[int, str]]:
        """The chain members that map every option this request sets, in order.

        Raises:
            NoAnswerProviderError: the chain is empty -- no answer backend has a key.
            UnsupportedParameterError: no member maps everything set, naming
                what each one lacked. Nothing has been asked.
        """
        from web_router.registry_answer import get_answer_provider_class

        self.require_members()
        wanted = request.specified_parameters()
        eligible: list[tuple[int, str]] = []
        skipped: list[str] = []
        unmapped: frozenset[str] = frozenset()
        for position, (provider_name, _) in enumerate(self._chain):
            lacks = wanted - get_answer_provider_class(provider_name).supported_parameters
            if not lacks:
                eligible.append((position, provider_name))
                continue
            skipped.append(
                MSG_AUTO_SKIPPED.format(name=provider_name, reasons=MSG_AUTO_UNMAPPED.format(names=_quoted(lacks)))
            )
            unmapped |= lacks
        if eligible:
            return eligible
        detail = MSG_AUTO_ANSWER_NO_MEMBER.format(skipped=", ".join(skipped))
        raise UnsupportedParameterError(self.name, unmapped, detail=detail)

    def close(self) -> None:
        for member in self._members.values():
            member.close()

    async def aclose(self) -> None:
        for member in self._members.values():
            await member.aclose()


def _empty_answer(response: AnswerResponse) -> bool:
    """A blank answer: nothing a caller could use."""
    return not (response.answer or "").strip()
