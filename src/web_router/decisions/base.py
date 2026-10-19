"""The decider slot: what any decision provider implements, and a wrapper that checks every answer it gives.

:class:`DecisionProvider` is exactly the shape :meth:`~web_router.WebRouter.classify`
already calls, so :class:`~web_router.decisions.JevDecider` fits as it is, and
so does a vendor's own class: ``decide(state, questions)`` answers each of
ROUTE_QUESTIONS with an :class:`~web_router.decisions.Answer`, and ``close()``
releases whatever the provider holds.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from datetime import date
from typing import Any, Protocol, runtime_checkable

from web_router.constants import (
    DECISION_CONFORMANCE_QUERIES,
    DECISION_ERROR_INVALID_ANSWER,
    DECISION_ERROR_MESSAGE_LIMIT,
    DECISION_STATUS_NOT_ASKED,
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
from web_router.decisions.schema import (
    Decision,
    DecisionError,
    Violation,
    error_kind,
    route_status,
    validate_answers,
)
from web_router.exceptions import ProviderError


@runtime_checkable
class DecisionProvider(Protocol):
    """Anything that can answer ROUTE_QUESTIONS about a query.

    ``decide`` raises :class:`~web_router.exceptions.ProviderError` when it
    cannot answer; :meth:`~web_router.WebRouter.classify` then takes the
    default route and records ``decider_error``. Two optional attributes feed
    a :class:`~web_router.decisions.schema.Decision`: ``version``, naming the
    model or rules behind the answers, and ``timeout_s``, the time budget.
    """

    name: str

    def decide(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, Answer]: ...

    def close(self) -> None: ...


def decider_version(provider: Any) -> str:
    """What a :class:`~web_router.decisions.schema.Decision` records as ``decider_version`` for ``provider``.

    Its ``version`` if it has one, else its ``model`` -- what Jev is pinned to -- else its name.
    """
    return str(getattr(provider, "version", None) or getattr(provider, "model", None) or provider.name)


def decider_timeout(provider: Any) -> float | None:
    """The time budget, in seconds, a provider declares as ``timeout_s``; ``None`` when it declares none."""
    timeout = getattr(provider, "timeout_s", None)
    return float(timeout) if isinstance(timeout, (int, float)) else None


class _LastCall(threading.local):
    # Per thread: a router shared across threads must not hand one caller another's violations.
    query: Any = None
    violations: Sequence[Violation] = ()
    error: ProviderError | None = None
    kind: str | None = None


class ValidatedDecider:
    """Checks every answer a provider gives against the locked schema, and never lets a problem pass quietly.

    ``strict`` decides what a schema-breaking answer does to the route:

    - ``strict=True`` (the default): it becomes a :class:`~web_router.exceptions.ProviderError`,
      so :meth:`~web_router.WebRouter.classify` takes its ``decider_error``
      fallback rather than route on labels nobody can trust.
    - ``strict=False``: the labels that broke it are left out, so classify
      gives each its default and says ``low_confidence``, exactly as for a
      label the decider did not give; the valid labels still route. For
      measurement runs, which should record a bad answer rather than lose it.

    Either way the violations are kept: :attr:`last_violations` for this
    thread's latest call, :attr:`violations` for every call, and in the
    :class:`~web_router.decisions.schema.Decision` from :meth:`decision`.

    Any exception from the provider becomes a ``ProviderError`` (classify
    catches nothing else), and its kind -- timeout, HTTP error, invalid answer
    or other -- is recorded. Exception types in ``passthrough`` are re-raised
    as they are instead, out of classify too: a caller's policy guard, say,
    must stop the run rather than read as a default route.

    A valid answer passes through unchanged, so labels under
    ROUTE_MIN_CONFIDENCE still take classify's ``low_confidence`` fallback.
    The wrapper closes the provider it wraps.
    """

    def __init__(
        self,
        provider: DecisionProvider,
        *,
        strict: bool = True,
        passthrough: tuple[type[BaseException], ...] = (),
        version: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self.provider = provider
        self.name = provider.name
        self.strict = strict
        self.passthrough = passthrough
        self.version = version or decider_version(provider)
        self.timeout_s = timeout_s if timeout_s is not None else decider_timeout(provider)
        self.violations: list[Violation] = []
        self._violations_lock = threading.Lock()
        self._last = _LastCall()

    @property
    def last_violations(self) -> list[Violation]:
        return list(self._last.violations)

    @property
    def last_error(self) -> ProviderError | None:
        return self._last.error

    def decide(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, Answer]:
        """The provider's answer, once it has passed the schema; under ``strict=False``, its valid labels.

        Raises:
            ProviderError: if the provider failed, or (strict) its answer broke the schema.
            Any type in ``passthrough``: as the provider raised it.
        """
        last = self._last
        last.query = state.get("query") if isinstance(state, dict) else None
        last.violations, last.error, last.kind = (), None, None
        try:
            answers = self.provider.decide(state, questions)
        except self.passthrough:
            raise
        except ProviderError as exc:
            last.error, last.kind = exc, error_kind(exc)
            raise
        except Exception as exc:  # a third-party provider may raise anything; classify only catches ProviderError
            detail = MSG_DECISION_DECIDER_FAILED.format(detail=repr(exc))[:DECISION_VIOLATION_MSG_LIMIT]
            last.error, last.kind = ProviderError(self.name, detail, original=exc), error_kind(exc)
            raise last.error from exc
        _, found = validate_answers(answers, self.version)
        if not found:
            return answers
        last.violations = found
        with self._violations_lock:
            self.violations.extend(found)
        if self.strict:
            first = f"{found[0].field}: {found[0].msg}"
            last.error = ProviderError(self.name, MSG_DECISION_INVALID.format(count=len(found), first=first))
            last.kind = DECISION_ERROR_INVALID_ANSWER
            raise last.error
        broken = {violation.field.split(".", 1)[0] for violation in found}
        if DECISION_VIOLATION_ROOT in broken:
            return {}
        return {name: answer for name, answer in answers.items() if name not in broken}

    def decision(self, route: Route) -> Decision:
        """The locked record of a route that :meth:`~web_router.WebRouter.classify` just built with this decider.

        The error and violations recorded are this thread's latest call's, and
        only when that call was for ``route.query``: call it right after classify.
        """
        last = self._last
        mine = route.query == last.query and route_status(route) != DECISION_STATUS_NOT_ASKED
        error = None
        if mine and last.error is not None and last.kind is not None:
            message = str(last.error)[:DECISION_ERROR_MESSAGE_LIMIT]
            error = DecisionError.model_validate({"kind": last.kind, "message": message})
        return Decision.from_route(
            route,
            decider_id=self.name,
            decider_version=self.version,
            timeout_s=self.timeout_s,
            error=error,
            violations=last.violations if mine else (),
        )

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
