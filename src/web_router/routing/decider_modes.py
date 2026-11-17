"""What a decider reads and answers about vendor modes: each mode's option text, and attempts ranked by score.

Experimental. Apart from :mod:`.decider_question` so each stays small. A calibrated decider spreads its
probability over near-duplicate options, so a backend and its modes are scored apart: the backend's own id
scores the backend, and each of its modes takes the share of the backend's mode scores it holds. An attempt
ranks by the product, ``backend x share``; a backend none of whose modes is scored ranks by its own score.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from web_router import registry
from web_router.constants_decider import DECIDER_DEPTH_DOES
from web_router.constants_modes import LEVELS, PART_SEP
from web_router.exceptions import ProviderNotFoundError, ProviderNotInstalledError
from web_router.routing.contract import Candidate, candidate_id


def mode_does(provider: str, mode: str) -> str:
    """What ``mode`` of ``provider`` does, part by part, from its declared ``does`` lines; a part with none by name."""
    try:
        cls = registry.get_provider_class(provider)
    except (ProviderNotFoundError, ProviderNotInstalledError):  # not registered here: its parts read by name
        cls = None
    declared = {**getattr(cls, "tiers", {}), **getattr(cls, "contents", {}), **getattr(cls, "flags", {})}
    return "; ".join(_part_does(part, declared) for part in mode.split(PART_SEP))


def _part_does(part: str, declared: Mapping[str, object]) -> str:
    if part in LEVELS:
        return DECIDER_DEPTH_DOES.format(level=part)
    does = getattr(declared.get(part), "does", "")
    return does if isinstance(does, str) and does else part


def by_score(
    candidates: Mapping[str, Candidate], ranked: Sequence[str], scores: Mapping[str, float] | None
) -> list[str]:
    """``ranked`` as attempts, best first: by ``backend x share`` when every ranked id has a score and every ranked
    mode's backend is ranked too, else in the decider's order. A backend whose modes are scored runs in them only;
    ties keep the decider's order. Methods keep the order the decider first names them in, so a backend's score split
    across its modes never hands the call to a method ranked below it."""
    if scores is None or any(cid not in scores for cid in ranked):
        return list(ranked)
    modes: dict[str, list[str]] = {}
    for cid in ranked:
        found = candidates[cid]
        if found.mode is not None:
            modes.setdefault(candidate_id(found.tool, found.provider), []).append(cid)
    if any(backend not in scores for backend in modes):
        return list(ranked)
    ranked_by: dict[str, float] = {}
    for cid in ranked:
        if candidates[cid].mode is None and cid not in modes:
            ranked_by[cid] = scores[cid]
    for backend, ids in modes.items():
        total = sum(scores[cid] for cid in ids)
        for cid in ids:
            ranked_by[cid] = scores[backend] * (scores[cid] / total if total else 0.0)
    methods = list(dict.fromkeys(candidates[cid].tool for cid in ranked))
    return sorted(ranked_by, key=lambda cid: (methods.index(candidates[cid].tool), -ranked_by[cid], ranked.index(cid)))
