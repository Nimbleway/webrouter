"""The noise floor each method is ranked with, and the rerun that measured it.

A method with a frozen rerun (:data:`~route_eval_sets.RERUNS`) is ranked with
that rerun's spread; one with none borrows search's, and says so. The spread
itself is :func:`route_eval_rank.noise_floor`. Imported as a sibling, by name,
as ``_common`` is.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from frozen import FROZEN_DIR
from route_eval_rank import noise_floor
from route_eval_sets import FALLBACK_FLOOR, RERUNS


@dataclass(frozen=True)
class Floor:
    """The noise floor one method is ranked with, and the rerun that measured it."""

    method: str
    value: float
    measured_on: str | None
    """The method whose rerun gave ``value``; ``None`` when the caller set it."""
    rerun: tuple[str, ...] = ()

    @property
    def fallback(self) -> bool:
        """Measured on another method's rerun: this one has none frozen."""
        return self.measured_on is not None and self.measured_on != self.method

    @classmethod
    def from_json(cls, method: str, data: Mapping[str, Any]) -> Floor:
        """The floor :meth:`as_json` wrote, as a set's summary holds it."""
        return cls(method, data["value"], data["measured_on"], tuple(data["rerun"]))

    def as_json(self, ndigits: int | None = None) -> dict[str, Any]:
        value = self.value if ndigits is None else round(self.value, ndigits)
        return {"value": value, "measured_on": self.measured_on, "rerun": list(self.rerun), "fallback": self.fallback}

    def describe(self) -> str:
        """The floor in words, for a report: its value and where it came from."""
        if self.measured_on is None:
            return f"{self.value * 100:.1f} points, as given"
        twins = " vs ".join(f"`{name}`" for name in self.rerun)
        if self.fallback:
            return f"{self.value * 100:.1f} points, {self.measured_on}'s ({twins}): no rerun of {self.method} is frozen"
        return f"{self.value * 100:.1f} points, {self.method}'s own rerun ({twins})"


def floor_for(method: str, folder: Path = FROZEN_DIR) -> Floor:
    """The floor ``method`` is ranked with: its own rerun's spread when one is frozen, else search's."""
    measured_on = method if method in RERUNS else FALLBACK_FLOOR
    twins = RERUNS[measured_on]
    return Floor(method, noise_floor(folder, twins)[0], measured_on, twins)
