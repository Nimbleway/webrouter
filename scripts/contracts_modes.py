"""A contract's vendor modes: the wire change each declared part makes, what a router is offered, and their keys.

``options.mode`` is a flat map from each tier, content and flag to the wire change pinning it alone makes,
probed like any other option. ``offers`` sits outside ``options``, since it names combinations rather
than values. ``verified`` keeps one ``mode.<part>`` key per declared part beside the option keys.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from web_router.providers._modes import offers

MODE = "mode"
VERIFIED_MODE = MODE + ".{part}"


def parts(cls: type) -> list[str]:
    """Every part ``cls`` declares: its tiers, then contents, then flags."""
    return [*getattr(cls, "tiers", {}), *getattr(cls, "contents", {}), *getattr(cls, "flags", {})]


def add_modes(contract: dict[str, Any], cls: type, probe: Callable[[str], dict[str, Any]]) -> None:
    """Add ``options.mode`` (each part's probed wire change) and ``offers``; nothing for a class with no parts."""
    declared = parts(cls)
    if declared:
        contract["options"][MODE] = {part: probe(part) for part in declared}
        contract["offers"] = list(offers(cls))


def kept_verified(contract: dict[str, Any], label: str) -> dict[str, str]:
    """``verified`` without the keys the contract no longer has: an option it stopped mapping, a part it dropped.

    It may hold each mapped option, and ``mode.<part>`` for each declared part.
    """
    modes = contract["options"].get(MODE, {})
    known = (set(contract["options"]) - {MODE}) | {VERIFIED_MODE.format(part=part) for part in modes}
    if dropped := sorted(set(contract["verified"]) - known):
        print(f"{label}: no longer maps {', '.join(dropped)}; dropping its verified entry", file=sys.stderr)
    return {key: how for key, how in contract["verified"].items() if key in known}
