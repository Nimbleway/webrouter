"""One routed call's hop trail: every candidate it moved past, in the order it happened.

Experimental. The :class:`~web_router.routing.ledger.Ledger` keeps one and
writes it into the decision record's ``hops``: a skip with the budget's
reason, a failure by its status or error class, an empty answer with none.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from web_router.types_decision import DecisionHop


@dataclass(frozen=True)
class Passed:
    """A candidate moved past: the attempt it was, its id, and why."""

    attempt: int | None
    """Its index in the ledger's attempts; ``None`` for a skip, which never started."""
    id: str
    why: str | None
    """The skip reason or failure code; ``None`` for an empty answer."""


@dataclass
class HopTrail:
    """The candidates one call moved past, by attempt, so a backend a chain names twice keeps both."""

    passed: list[Passed] = field(default_factory=list)
    open: Passed | None = None
    """The last attempt, until the call fails it or moves past it: an empty answer, unless it answered."""

    def start(self, attempt: int, id: str) -> None:
        """Attempt ``attempt`` (``id``) starts; the one before it, still open, answered empty."""
        self.moved_on()
        self.open = Passed(attempt, id, None)

    def skip(self, id: str, why: str) -> None:
        """``id`` was skipped before it started, for ``why``."""
        self.moved_on()
        self.passed.append(Passed(None, id, why))

    def fail(self, id: str, why: str) -> None:
        """``id`` failed with ``why``: the open attempt when it is that one, else a failure never charged."""
        attempt = self.open.attempt if self.open is not None and self.open.id == id else None
        self.passed.append(Passed(attempt, id, why))
        self.open = None if attempt is not None else self.open

    def moved_on(self) -> None:
        """The call went on past the open attempt without its failing: it answered empty."""
        if self.open is not None:
            self.passed.append(self.open)
            self.open = None

    def answered_empty(self) -> list[int]:
        """The attempts moved past with an empty answer, in order."""
        return [hop.attempt for hop in self.passed if hop.why is None and hop.attempt is not None]

    def hops(self, answered: int | None) -> list[DecisionHop] | None:
        """Every candidate moved past, in order: the open attempt too, when another's held answer was returned."""
        if self.open is not None and self.open.attempt != answered:
            self.moved_on()
        kept = [hop for hop in self.passed if not (hop.attempt == answered and hop.why is None)]
        return [DecisionHop(id=hop.id, error=hop.why) for hop in kept] or None
