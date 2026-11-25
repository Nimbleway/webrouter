"""Lookup tables for the data-driven router's import tests: one row per measured arm."""

from __future__ import annotations

from typing import Any

MEASURES = {"quality": 1.0, "ci95": 1.0, "usd_per_1k": 1.0, "p50_s": 1.0}


def row(name: str, effort: str = "base", **call: Any) -> dict[str, Any]:
    """A lookup row for ``name`` at ``effort``; ``call`` is what the delivery says it was measured with."""
    made = {"arm": f"{name}.search@{effort}", "provider": name, "effort": effort, **MEASURES}
    return made | ({"call": {"provider": name, **call}} if call else {})


def lookup(*rows: dict[str, Any]) -> dict[str, Any]:
    """A delivered lookup table holding ``rows`` as its search arms."""
    return {"version": "v", "source": "s", "judge": "j", "tools": {"search": {"all": list(rows)}}}
