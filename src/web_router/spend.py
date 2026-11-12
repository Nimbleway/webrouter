"""A spend log: one line per call a router answered, and a report of it by backend, method and day.

``router.log_spend("spend.jsonl")`` appends a :class:`SpendEntry` to that file
after every search, extract, answer, research and browse call the router
answers -- budget or not -- and returns the :class:`SpendLog`, whose
:meth:`~SpendLog.report` sums it. Any object with ``record(entry)`` is a sink
of its own (a database, a metrics client): pass it instead of a path.

What it logs is what the response says: the backend that answered, its
``cost_usd`` and whether that was ``reported`` or ``estimated``, and the
seconds the call took. With a budget or a router, ``call_estimate_usd`` is the
decision record's estimate for the whole call, attempts that failed before the
answer included; otherwise it is the answer's own estimate. A call that raised
logs nothing. The file holds no prompt, query or URL, only the decision's id.

A sink that raises does not fail the call: the router logs a warning on the
``web_router`` logger and returns the response.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from web_router.constants_cost import COST_REPORTED
from web_router.constants_spend import MSG_SPEND_REPORT_KEY, SPEND_REPORT_KEYS
from web_router.exceptions_typed import InvalidParameterError
from web_router.types_cost import CostSource

SpendKey = Literal["day", "provider", "method", "cost_source"]
"""What a report can group by."""

DEFAULT_KEYS: tuple[SpendKey, ...] = ("day", "provider", "method")


class SpendEntry(BaseModel):
    """One call a router answered: when, which method and backend, what it cost and took."""

    model_config = ConfigDict(extra="forbid")

    at: datetime = Field(description="When the call answered, in UTC.")
    method: str = Field(description="search, extract, answer, research or browse.")
    provider: str = Field(description="The backend that answered.")
    cost_usd: float | None = Field(default=None, description="The response's cost_usd; None when unknown.")
    cost_source: CostSource | None = Field(default=None, description="reported or estimated.")
    call_estimate_usd: float | None = Field(
        default=None, description="The decision record's estimate for the whole call, failed attempts included."
    )
    elapsed_s: float | None = Field(default=None, description="Wall-clock seconds the call took.")
    decision_id: str | None = Field(default=None, description="The decision record's id, when it has one.")

    @property
    def day(self) -> date:
        return self.at.astimezone(UTC).date()


@runtime_checkable
class SpendSink(Protocol):
    """Where a router's spend log goes: anything with ``record``."""

    def record(self, entry: SpendEntry) -> None: ...


class SpendRow(BaseModel):
    """One group of a spend report: its key, the calls in it and what they cost."""

    model_config = ConfigDict(extra="forbid")

    key: dict[str, str] = Field(description="The group's value for each key the report grouped by.")
    calls: int = 0
    cost_usd: float = Field(default=0.0, description="Every known cost in the group, summed.")
    reported_usd: float = Field(default=0.0, description="The part of cost_usd the backends stated.")
    estimated_usd: float = Field(default=0.0, description="The part of cost_usd estimated from list prices.")
    unpriced_calls: int = Field(default=0, description="Calls with no known cost, left out of cost_usd.")
    elapsed_s: float = Field(default=0.0, description="Seconds the calls took, summed.")


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
            with self.path.open("a", encoding="utf-8") as file:
                file.write(line)

    def entries(self, since: date | None = None) -> list[SpendEntry]:
        """Every entry, oldest first; from ``since`` (a UTC day) on when it is set. A missing file has none."""
        if not self.path.exists():
            return []
        with self._lock:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        read = [SpendEntry.model_validate(json.loads(line)) for line in lines if line.strip()]
        return [entry for entry in read if since is None or entry.day >= since]

    def report(self, by: Sequence[SpendKey] = DEFAULT_KEYS, since: date | None = None) -> list[SpendRow]:
        """The log summed by ``by``, as :func:`spend_report` sums it."""
        return spend_report(self.entries(since), by)


def spend_report(entries: Iterable[SpendEntry], by: Sequence[SpendKey] = DEFAULT_KEYS) -> list[SpendRow]:
    """``entries`` grouped by ``by`` -- any of day, provider, method and cost_source -- each group summed.

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
    row.calls += 1
    row.elapsed_s += entry.elapsed_s or 0.0
    if entry.cost_usd is None:
        row.unpriced_calls += 1
        return
    row.cost_usd += entry.cost_usd
    if entry.cost_source == COST_REPORTED:
        row.reported_usd += entry.cost_usd
    else:
        row.estimated_usd += entry.cost_usd
