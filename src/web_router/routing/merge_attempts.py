"""A decision's ``attempts``: the candidates to try, best first, which replace its ``tools`` and ``providers``.

Experimental. Apart from :mod:`.merge` so each stays small. An attempt names a
candidate by id, a vendor mode's included (``search:exa:exa-instant``). The
merged order names each as an *entry*: its backend, or with a mode
``exa:exa-instant``, which ``auto`` runs as that backend in that mode
(:func:`~web_router.routing.attempts.in_call_order`). One backend may so run in
several modes. Excluding a backend's id excludes its modes too; excluding a
mode's id excludes that mode only. Which exclusions count is decided here
alone, for the merge and for the cost order :mod:`.decide` starts it from.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence

from web_router.constants_merge import MSG_UNKNOWN_ID
from web_router.routing.contract import Candidate, RouteDecision, RouteRequest, candidate_id
from web_router.routing.entry_names import entry_name


def entry(candidate: Candidate) -> str:
    """How the merged order names ``candidate``: its backend, or ``backend:mode`` for a vendor mode."""
    return entry_name(candidate.provider, candidate.mode)


def backends(request: RouteRequest, tool: str, names: Sequence[str]) -> tuple[str, ...]:
    """The backends ``names``, entries in the merged order, run on, each once: ``exa`` for ``exa:exa-instant``."""
    by_entry = {entry(c): c.provider for c in request.candidates if c.tool == tool}
    return tuple(dict.fromkeys(by_entry.get(name, name) for name in names))


def only_in_modes(request: RouteRequest, tool: str, names: Sequence[str]) -> frozenset[str]:
    """The backends ``names`` run only in vendor modes: named in a mode, never bare."""
    bare = {c.provider for c in request.candidates if c.tool == tool and c.is_backend}
    return frozenset(backends(request, tool, names)) - {name for name in names if name in bare}


def is_excluded(candidate: Candidate, excluded: Collection[str]) -> bool:
    """Whether ``excluded`` names ``candidate``, or for a vendor mode, its backend."""
    return candidate.id in excluded or candidate_id(candidate.tool, candidate.provider) in excluded


def _by_entry(candidates: Sequence[Candidate], tool: str, excluded: Collection[str]) -> dict[str, Candidate]:
    return {entry(c): c for c in candidates if c.tool == tool and not is_excluded(c, excluded)}


def entries(request: RouteRequest, tool: str, excluded: Collection[str]) -> dict[str, Candidate]:
    """``tool``'s candidates that are not excluded, by entry, in the request's order."""
    return _by_entry(request.candidates, tool, excluded)


def in_modes(candidates: Sequence[Candidate], tool: str, ranked: Sequence[str]) -> set[str]:
    """The backends ``ranked`` names in a vendor mode, excluded or not: none runs at its default mode unranked."""
    named = _by_entry(candidates, tool, ())
    return {named[name].provider for name in ranked if name in named and not named[name].is_backend}


def excludable(candidates: Sequence[Candidate]) -> set[str]:
    """The ids an exclusion may name: a candidate's, and each backend's, whose modes it excludes too."""
    return {c.id for c in candidates} | {candidate_id(c.tool, c.provider) for c in candidates}


def reasoned(candidates: Sequence[Candidate], exclude: Mapping[str, str]) -> dict[str, str]:
    """The exclusions that count before rule 3: each of an id :func:`excludable` names, with a reason."""
    known = excludable(candidates)
    return {cid: why for cid, why in exclude.items() if cid in known and why.strip()}


def kept_exclusions(
    candidates: Sequence[Candidate], tool: str, ranked: Sequence[str], excluded: Mapping[str, str]
) -> dict[str, str]:
    """Rule 3: ``excluded`` whole, unless it leaves ``tool`` nothing to run -- no ``ranked`` entry, and no
    backend :func:`in_modes` leaves out. Then the ranked modes excluded by their own id run again and every other
    exclusion holds; with no such mode, none of ``tool``'s holds."""
    pool, moded = _by_entry(candidates, tool, excluded), in_modes(candidates, tool, ranked)
    if any(name in pool for name in ranked) or any(c.is_backend and c.provider not in moded for c in pool.values()):
        return dict(excluded)
    named = [c for name, c in _by_entry(candidates, tool, ()).items() if name in ranked and not c.is_backend]
    lift = {c.id for c in named if candidate_id(tool, c.provider) not in excluded}
    lift = lift or {cid for cid in excluded if cid.startswith(candidate_id(tool, ""))}
    return {cid: why for cid, why in excluded.items() if cid not in lift}


def ranks(candidates: Sequence[Candidate], tool: str, attempts: Sequence[str], excluded: Mapping[str, str]) -> bool:
    """Whether the merge keeps one of ``attempts`` of ``tool`` once ``excluded`` (:func:`reasoned`) is applied."""
    known = {c.id: c for c in candidates}
    named = [known[cid] for cid in dict.fromkeys(attempts) if cid in known and known[cid].tool == tool]
    kept = kept_exclusions(candidates, tool, [entry(c) for c in named], excluded)
    return any(not is_excluded(c, kept) for c in named)


def first_tool(request: RouteRequest, decision: RouteDecision, excluded: Collection[str]) -> str | None:
    """The method of the first attempt that names a candidate not excluded; ``None`` when none does."""
    known = {c.id: c for c in request.candidates}
    for cid in decision.attempts or ():
        found = known.get(cid)
        if found is not None and not is_excluded(found, excluded):
            return found.tool
    return None


def ranked_attempts(
    tool: str, request: RouteRequest, decision: RouteDecision, ignored: dict[str, str]
) -> tuple[str, ...]:
    """The decision's attempts for ``tool``, as entries, best first; an unknown id noted and left out."""
    known = {c.id: c for c in request.candidates}
    ranked = []
    for cid in dict.fromkeys(decision.attempts or ()):
        found = known.get(cid)
        if found is None:
            ignored[cid] = MSG_UNKNOWN_ID.format(id=cid)
        elif found.tool == tool:
            ranked.append(entry(found))
    return tuple(ranked)
