"""A spend log: one line per call a router answered or attempt that failed, and a report of it.

``router.log_spend("spend.jsonl")`` appends a :class:`SpendEntry` to that file
after every search, extract, answer, research and browse call the router
answers -- budget or not -- and returns the :class:`SpendLog`, whose
:meth:`~SpendLog.report` sums it. Any object with ``record(entry)`` is a sink
of its own (a database, a metrics client): pass it instead of a path.

What it logs is what the response says: the backend that answered, its
``cost_usd`` and whether that was ``reported`` or ``estimated``, and the
seconds the call took. With a budget or a router, ``call_estimate_usd`` is the
decision record's estimate for the whole call, attempts that failed before the
answer included; otherwise it is the answer's own estimate. Each attempt that
failed or answered empty before the answer has a line of its own, with its
``outcome`` and ``error``, and so does a call that raised: a backend may bill a
timeout. The file holds no prompt, query or URL, only the decision's id.

A sink that raises does not fail the call: the router logs a warning on the
``web_router`` logger and returns the response.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from web_router.constants_cost import COST_REPORTED
from web_router.constants_spend import MSG_SPEND_LINE_SKIPPED, MSG_SPEND_REPORT_KEY, OUTCOME_ANSWERED, SPEND_REPORT_KEYS
from web_router.exceptions_typed import InvalidParameterError
from web_router.types_cost import CostSource

SpendKey = Literal["day", "provider", "method", "cost_source", "outcome"]
"""What a report can group by."""

DEFAULT_KEYS: tuple[SpendKey, ...] = ("day", "provider", "method")

SpendOutcome = Literal["answered", "failed", "empty"]
"""What a logged attempt came to: the call's answer, a failure, or an empty answer the call moved past."""


class SpendEntry(BaseModel):
    """One call a router answered, or one attempt that did not: when, which method and backend, cost and time."""

    model_config = ConfigDict(extra="forbid")

    at: datetime = Field(description="When the call answered, in UTC.")
    method: str = Field(description="search, extract, answer, research or browse.")
    provider: str = Field(description="The backend that answered.")
    cost_usd: float | None = Field(
        default=None, allow_inf_nan=False, description="The response's cost_usd; None when unknown."
    )
    cost_source: CostSource | None = Field(default=None, description="reported or estimated.")
    call_estimate_usd: float | None = Field(
        default=None,
        allow_inf_nan=False,
        description="The decision record's estimate for the whole call, failed attempts included.",
    )
    elapsed_s: float | None = Field(default=None, allow_inf_nan=False, description="Wall-clock seconds the call took.")
    decision_id: str | None = Field(default=None, description="The decision record's id, when it has one.")
    outcome: SpendOutcome = Field(default=OUTCOME_ANSWERED, description="answered, failed or empty.")
    error: str | None = Field(default=None, description="Why a failed attempt failed: its HTTP status or error kind.")

    @field_validator("at")
    @classmethod
    def _in_utc(cls, at: datetime) -> datetime:
        """A naive time is read as UTC, as the field says, never as the host's own zone."""
        return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)

    @property
    def day(self) -> date:
        return self.at.date()


@runtime_checkable
class SpendSink(Protocol):
    """Where a router's spend log goes: anything with ``record``.

    ``record`` runs inside the call, on the event loop for an async router: it must not block.
    """

    def record(self, entry: SpendEntry) -> None: ...


class SpendRow(BaseModel):
    """One group of a spend report: its key, the calls in it and what they cost."""

    model_config = ConfigDict(extra="forbid")

    key: dict[str, str] = Field(description="The group's value for each key the report grouped by.")
    calls: int = Field(default=0, description="Calls answered.")
    failed_attempts: int = Field(default=0, description="Attempts that failed or answered empty.")
    cost_usd: float = Field(default=0.0, description="Every known cost in the group, failed attempts' too, summed.")
    reported_usd: float = Field(default=0.0, description="The part of cost_usd the backends stated.")
    estimated_usd: float = Field(default=0.0, description="The part of cost_usd estimated from list prices.")
    unpriced_calls: int = Field(default=0, description="Calls with no known cost, left out of cost_usd.")
    elapsed_s: float = Field(
        default=0.0,
        description=(
            "Seconds the answered calls took, the failed attempts they moved past included. "
            "A failed line's seconds stay on that line and are not summed here."
        ),
    )


class SpendLog:
    """A spend log in a JSON-lines file: :meth:`record` appends, :meth:`entries` reads, :meth:`report` sums.

    Safe to share between threads and routers in one process: each line is
    written whole, under a lock.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self._lock = threading.Lock()

    def record(self, entry: SpendEntry) -> None:
        line = entry.model_dump_json() + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a+b") as file:
                if file.seek(0, 2) and (file.seek(-1, 2), file.read(1))[1] != b"\n":
                    line = "\n" + line  # a write cut short left the last line open: never join the next one to it
                file.write(line.encode("utf-8"))

    def entries(self, since: date | None = None) -> list[SpendEntry]:
        """Every entry, oldest first; from ``since`` (a UTC day) on when it is set. A missing file has none."""
        if not self.path.exists():
            return []
        with self._lock:
            # "replace": a character an interrupted write cut in half fails its own line, not the whole file
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        read = [entry for line in lines if line.strip() and (entry := _parsed(line)) is not None]
        return [entry for entry in read if since is None or entry.day >= since]

    def report(self, by: Sequence[SpendKey] = DEFAULT_KEYS, since: date | None = None) -> list[SpendRow]:
        """The log summed by ``by``, as :func:`spend_report` sums it."""
        return spend_report(self.entries(since), by)


def _parsed(line: str) -> SpendEntry | None:
    """The entry one log line holds; ``None``, with a warning, for a line an interrupted write left unfinished."""
    try:
        return SpendEntry.model_validate(json.loads(line))
    except (ValueError, ValidationError):
        logging.getLogger("web_router").warning(MSG_SPEND_LINE_SKIPPED, line)
        return None


def spend_report(entries: Iterable[SpendEntry], by: Sequence[SpendKey] = DEFAULT_KEYS) -> list[SpendRow]:
    """``entries`` grouped by ``by`` -- any of day, provider, method, cost_source and outcome -- each summed.

    Rows come sorted by their key. Raises :class:`~web_router.InvalidParameterError` for another key.
    """
    for key in by:
        if key not in SPEND_REPORT_KEYS:
            raise InvalidParameterError("by", MSG_SPEND_REPORT_KEY.format(key=key, keys=", ".join(SPEND_REPORT_KEYS)))
    rows: dict[tuple[str, ...], SpendRow] = {}
    for entry in entries:
        values = tuple(_value(entry, key) for key in by)
        row = rows.setdefault(values, SpendRow(key=dict(zip(by, values, strict=True))))
        _add(row, entry)
    return [rows[key] for key in sorted(rows)]


def _value(entry: SpendEntry, key: str) -> str:
    value: Any = entry.day.isoformat() if key == "day" else getattr(entry, key)
    return "" if value is None else str(value)


def _add(row: SpendRow, entry: SpendEntry) -> None:
    answered = entry.outcome == OUTCOME_ANSWERED
    row.calls += answered
    row.failed_attempts += not answered
    if answered:  # an attempt's seconds are already in its call's own line
        row.elapsed_s += entry.elapsed_s or 0.0
    if entry.cost_usd is None:
        row.unpriced_calls += answered
        return
    row.cost_usd += entry.cost_usd
    if entry.cost_source == COST_REPORTED:
        row.reported_usd += entry.cost_usd
    else:
        row.estimated_usd += entry.cost_usd
