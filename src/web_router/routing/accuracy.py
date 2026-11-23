"""A router's own accuracy scores: what ``accuracy=`` takes, checked once, and each candidate's score.

Experimental. web-router ships no scores for routers: a router that ranks by
accuracy holds the caller's, read from a mapping or a JSON file of the same
shape -- per method, a backend's score, or one vendor mode's::

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
from pathlib import Path

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
from web_router.routing.contract import Candidate, ToolName
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
        if isinstance(score, bool) or not isinstance(score, int | float) or not 0 <= score <= 1:  # NaN fails too
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
