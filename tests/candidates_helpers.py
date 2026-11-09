"""What the candidate builder's tests share: a keyless environment, keys set by backend, and readers of the result.

Offline: the members are read from their classes and the shipped price list;
nothing is built that sends, and no key is real.
"""

from __future__ import annotations

import pytest

from web_router.constants import (
    ANSWER_ENV_BY_PROVIDER,
    BROWSE_ENV_BY_PROVIDER,
    ENV_BY_PROVIDER,
    EXTRACT_ENV_BY_PROVIDER,
    RESEARCH_ENV_BY_PROVIDER,
)
from web_router.routing.candidates import CandidateSet
from web_router.routing.contract import Candidate

_TABLES = (ANSWER_ENV_BY_PROVIDER, BROWSE_ENV_BY_PROVIDER, EXTRACT_ENV_BY_PROVIDER, RESEARCH_ENV_BY_PROVIDER)
_ALL_ENV = {var for table in _TABLES for names in table.values() for var in names} | {
    var for var in ENV_BY_PROVIDER.values() if var
}


def clear_keys(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Take every backend key out of the environment, for a test's ``keys`` fixture; it sets the ones it needs."""
    for var in _ALL_ENV:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def set_keys(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Give each named backend a test key for every method it serves."""
    for name in names:
        for table in _TABLES:
            for var in table.get(name, ()):
                monkeypatch.setenv(var, "test-key")
        if search_var := ENV_BY_PROVIDER.get(name):
            monkeypatch.setenv(search_var, "test-key")


def ids(found: CandidateSet, tool: str | None = None) -> list[str]:
    """The ids of the candidates found, in order, for ``tool`` alone when given."""
    return [c.id for c in found.candidates if tool is None or c.tool == tool]


def one(found: CandidateSet, candidate_id: str) -> Candidate:
    """The candidate found with ``candidate_id``."""
    return next(c for c in found.candidates if c.id == candidate_id)
