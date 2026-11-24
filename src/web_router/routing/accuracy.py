"""A router's own accuracy scores: what ``accuracy=`` takes, checked once, and each candidate's score.

Experimental. A router at ``accurate`` holds the caller's scores, read from a
mapping or a JSON file of the same shape -- per method, a backend's score, or
one vendor mode's -- or, given none, the shipped ones (:func:`shipped_scores`)::

    {"search": {"exa": 0.161, "exa:exa-deep": 0.224}, "answer": {"perplexity": 0.953}}

A score is the share of a benchmark's questions answered correctly, from 0 to
1, and compares only within its method. A ``provider`` key scores the backend
as the request sends it; ``provider:mode`` scores that mode. Anything not
listed is unknown, and so is a keyless tier or a member with its own options
(``Candidate.measured`` is ``False``): the score describes another backend.
"""

from __future__ import annotations

import importlib
import json
import os
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from web_router._chains import is_accuracy_share, measured_accuracy
from web_router.constants_modes import PART_SEP
from web_router.constants_prefer import (
    MSG_ACCURACY_FILE,
    MSG_ACCURACY_METHOD,
    MSG_ACCURACY_MODE,
    MSG_ACCURACY_MODE_SPELLING,
    MSG_ACCURACY_MODE_UNKNOWN,
    MSG_ACCURACY_PROVIDER,
    MSG_ACCURACY_SCORE,
    MSG_ACCURACY_SHAPE,
)
from web_router.exceptions import ConfigurationError
from web_router.routing.contract import Candidate, Prefer, RouteRequest, ToolName
from web_router.routing.route_tools import ALL_TOOLS

AccuracyScores = Mapping[ToolName, Mapping[str, float]]
"""Per method, a score by ``provider`` or ``provider:mode``."""

AccuracyLike = Mapping[str, Mapping[str, float]] | str | os.PathLike[str]
"""What ``accuracy=`` takes: the scores, or the path of a JSON file holding them."""

_BACKENDS: dict[ToolName, tuple[str, str]] = {
    "search": ("web_router.registry", "available_providers"),
    "extract": ("web_router.registry", "available_extract_providers"),
    "answer": ("web_router.registry_answer", "available_answer_providers"),
    "research": ("web_router.registry", "available_research_providers"),
    "browse": ("web_router.registry", "available_browse_providers"),
}
"""Where each method's registered backends are listed, read when scores are checked: the registry imports routing."""


SHIPPED_METHODS: tuple[ToolName, ...] = ("search", "answer")
"""The methods whose backends the benchmark behind ``auto``'s order measured."""


def router_scores(prefer: Prefer | None, accuracy: AccuracyLike | None) -> AccuracyScores | None:
    """The scores a router holds: ``accuracy`` read and checked when given; else, at ``accurate``, the shipped ones.

    The caller's scores replace the shipped ones whole: scores from two benchmarks do not compare.
    """
    if accuracy is None and prefer == "accurate":
        return shipped_scores()
    return accuracy_scores(accuracy)


def live_scores(prefer: Prefer | None, caller: AccuracyScores | None) -> AccuracyScores | None:
    """The caller's parsed scores, else the live shipped scores when accuracy is preferred."""
    return router_scores(prefer, None) if caller is None else caller


def scoped(
    req: RouteRequest, prefer: Prefer | None, caller: AccuracyScores | None
) -> tuple[RouteRequest, AccuracyScores | None]:
    """This call's live scores and custom-candidate eligibility, on a request of its own."""
    scores = live_scores(prefer, caller)
    return scored_request(req, scores), scores


def shipped_scores() -> AccuracyScores:
    """Search's and answer's backends, at their default mode, as the benchmark behind ``auto``'s order measured
    them (``data/chains.json``): the share of its questions each answered correctly. No vendor mode is scored."""
    from web_router.registry import is_custom  # registry imports routing while loading

    return {
        method: {name: score for name, score in measured_accuracy(method).items() if not is_custom(method, name)}
        for method in SHIPPED_METHODS
    }


def scored_request(req: RouteRequest, scores: AccuracyScores | None) -> RouteRequest:
    """Mark custom candidates against this router's scores without changing keyless/options eligibility."""
    from web_router.registry import is_custom  # registry imports routing while loading

    if scores is None:
        return req
    return replace(
        req,
        candidates=tuple(
            replace(c, measured=c.measured and score_of(scores, c) is not None) if is_custom(c.tool, c.provider) else c
            for c in req.candidates
        ),
    )


def accuracy_scores(accuracy: AccuracyLike | None) -> AccuracyScores | None:
    """``accuracy`` read and checked; ``None`` for none.

    Raises:
        ConfigurationError: for a file that is not JSON, a shape other than method to key to score, a method that
            is not routed, a backend that method does not have, a mode the backend does not offer or does not spell
            that way, or a score that is not a finite number from 0 to 1.
    """
    if accuracy is None:
        return None
    raw = _read(accuracy) if isinstance(accuracy, str | os.PathLike) else accuracy
    if not isinstance(raw, Mapping):
        raise ConfigurationError(MSG_ACCURACY_SHAPE.format(got=type(raw).__name__, where="the top level"))
    return {_method(method): _checked(method, keys) for method, keys in raw.items()}


def score_of(scores: AccuracyScores | None, candidate: Candidate) -> float | None:
    """``candidate``'s score in ``scores``; ``None`` when unknown or when it is not the backend a score measures."""
    if scores is None or not candidate.measured:
        return None
    key = candidate.provider if candidate.mode is None else f"{candidate.provider}:{candidate.mode}"
    return scores.get(candidate.tool, {}).get(key)


def _read(path: str | os.PathLike[str]) -> object:
    try:
        return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(MSG_ACCURACY_FILE.format(path=os.fspath(path), why=type(exc).__name__)) from None


def _method(method: object) -> ToolName:
    for tool in ALL_TOOLS:
        if tool == method:
            return tool
    raise ConfigurationError(MSG_ACCURACY_METHOD.format(got=method, known=", ".join(sorted(ALL_TOOLS))))


def _checked(method: ToolName, keys: object) -> dict[str, float]:
    if not isinstance(keys, Mapping):
        raise ConfigurationError(MSG_ACCURACY_SHAPE.format(got=type(keys).__name__, where=method))
    out: dict[str, float] = {}
    for key, score in keys.items():
        if not isinstance(key, str):
            raise ConfigurationError(MSG_ACCURACY_SHAPE.format(got=key, where=method))
        _known(method, key)
        if not is_accuracy_share(score):  # NaN fails too
            raise ConfigurationError(MSG_ACCURACY_SCORE.format(key=key, method=method, got=score))
        out[key] = float(score)
    return out


def _known(method: ToolName, key: str) -> None:
    """Refuse a key whose backend ``method`` does not have, or whose mode that backend does not spell that way."""
    from web_router.prices_modes import canonical_parts  # here: both load the package, which imports routing

    module, listing = _BACKENDS[method]
    provider, colon, mode = key.partition(":")
    if provider not in getattr(importlib.import_module(module), listing)():
        raise ConfigurationError(MSG_ACCURACY_PROVIDER.format(got=provider, method=method))
    if not colon:
        return
    canonical = canonical_parts(provider, method, mode.split(PART_SEP))
    if canonical != mode:
        why = MSG_ACCURACY_MODE_UNKNOWN if canonical is None else MSG_ACCURACY_MODE_SPELLING.format(canonical=canonical)
        raise ConfigurationError(MSG_ACCURACY_MODE.format(got=mode, provider=provider, why=why))
