"""The decider slot: what any decision provider implements, and a wrapper that checks every answer it gives.

:class:`DecisionProvider` is exactly the shape :meth:`~web_router.WebRouter.classify`
already calls, so :class:`~web_router.decisions.JevDecider` fits as it is, and
so does a vendor's own class: ``decide(state, questions)`` answers each of
ROUTE_QUESTIONS with an :class:`~web_router.decisions.Answer`, and ``close()``
releases whatever the provider holds.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any, Protocol, runtime_checkable

from web_router.constants import (
    DECISION_CONFORMANCE_QUERIES,
    DECISION_VIOLATION_DECIDER_ERROR,
    DECISION_VIOLATION_MSG_LIMIT,
    DECISION_VIOLATION_NOT_A_PROVIDER,
    DECISION_VIOLATION_ROOT,
    MSG_DECISION_DECIDER_FAILED,
    MSG_DECISION_INVALID,
    MSG_DECISION_NOT_A_PROVIDER,
    ROUTE_QUESTIONS,
)
from web_router.decisions.route import Answer, Route
from web_router.decisions.schema import Decision, Violation, validate_answers
from web_router.exceptions import ProviderError


@runtime_checkable
class DecisionProvider(Protocol):
    """Anything that can answer ROUTE_QUESTIONS about a query.

    ``decide`` raises :class:`~web_router.exceptions.ProviderError` when it
    cannot answer; :meth:`~web_router.WebRouter.classify` then takes the
    default route and records ``decider_error``. An optional ``version``
    attribute names the model or rules behind the answers in a
    :class:`~web_router.decisions.schema.Decision`.
    """

    name: str

    def decide(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, Answer]: ...

    def close(self) -> None: ...


def decider_version(provider: Any) -> str:
    """What a :class:`~web_router.decisions.schema.Decision` records as ``decider_version`` for ``provider``.

    Its ``version`` if it has one, else its ``model`` -- what Jev and the OpenAI
    decider are pinned to -- else its name.
    """
    return str(getattr(provider, "version", None) or getattr(provider, "model", None) or provider.name)


class ValidatedDecider:
    """Checks every answer a provider gives against the locked schema, and never lets a bad one through quietly.

    An answer that breaks the schema, or a provider that raises anything at
    all, becomes a :class:`~web_router.exceptions.ProviderError`, so
    :meth:`~web_router.WebRouter.classify` takes its own ``decider_error``
    fallback rather than routing on labels nobody can trust. What was wrong
    stays readable: :attr:`last_violations` for the latest call,
    :attr:`violations` for every call, and :attr:`last_error` for the failure.

    A valid answer passes through unchanged, so labels under
    ROUTE_MIN_CONFIDENCE still take classify's ``low_confidence`` fallback.
    The wrapper closes the provider it wraps.
    """

    def __init__(self, provider: DecisionProvider, *, version: str | None = None) -> None:
        self.provider = provider
        self.name = provider.name
        self.version = version or decider_version(provider)
        self.last_violations: list[Violation] = []
        self.violations: list[Violation] = []
        self.last_error: ProviderError | None = None

    def decide(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, Answer]:
        """The provider's answer, once it has passed the schema.

        Raises:
            ProviderError: if the provider failed, or its answer broke the schema.
        """
        self.last_violations = []
        self.last_error = None
        try:
            answers = self.provider.decide(state, questions)
        except ProviderError as exc:
            self.last_error = exc
            raise
        except Exception as exc:  # a third-party provider may raise anything; classify only catches ProviderError
            detail = MSG_DECISION_DECIDER_FAILED.format(detail=repr(exc))[:DECISION_VIOLATION_MSG_LIMIT]
            self.last_error = ProviderError(self.name, detail, original=exc)
            raise self.last_error from exc
        _, found = validate_answers(answers, self.version)
        if found:
            self.last_violations = found
            self.violations.extend(found)
            first = f"{found[0].field}: {found[0].msg}"
            self.last_error = ProviderError(self.name, MSG_DECISION_INVALID.format(count=len(found), first=first))
            raise self.last_error
        return answers

    def decision(self, route: Route) -> Decision:
        """The locked record of a route that :meth:`~web_router.WebRouter.classify` built with this decider."""
        return Decision.from_route(route, decider_id=self.name, decider_version=self.version)

    def close(self) -> None:
        self.provider.close()


def conformance_violations(
    provider: DecisionProvider, queries: Sequence[str] = DECISION_CONFORMANCE_QUERIES
) -> list[Violation]:
    """Ask ``provider`` each query as classify would, and return every way it broke the contract.

    For a vendor's test suite: ``assert conformance_violations(MyDecider()) == []``.
    A provider that raises counts as a violation here, because a conformance run
    asks only what any provider should be able to answer. The provider is not closed.
    """
    if not isinstance(provider, DecisionProvider):
        kind = type(provider).__name__
        return [
            Violation(
                field=DECISION_VIOLATION_ROOT,
                value=repr(provider)[:DECISION_VIOLATION_MSG_LIMIT],
                decider=kind,
                msg=MSG_DECISION_NOT_A_PROVIDER.format(kind=kind),
                type=DECISION_VIOLATION_NOT_A_PROVIDER,
            )
        ]
    version = decider_version(provider)
    found: list[Violation] = []
    for query in queries:
        state = {"query": query, "asked_on": date.today().isoformat()}
        try:
            answers = provider.decide(state, ROUTE_QUESTIONS)
        except Exception as exc:  # reported, not raised: one failing query should not hide the others
            found.append(
                Violation(
                    field=DECISION_VIOLATION_ROOT,
                    value=repr(query),
                    decider=version,
                    msg=MSG_DECISION_DECIDER_FAILED.format(detail=repr(exc))[:DECISION_VIOLATION_MSG_LIMIT],
                    type=DECISION_VIOLATION_DECIDER_ERROR,
                )
            )
            continue
        found.extend(validate_answers(answers, version)[1])
    return found
