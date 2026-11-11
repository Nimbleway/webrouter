"""Deciders: the part of a router that answers one choice question, for :class:`~.decider_router.DeciderRouter`.

Experimental. A decision API, a classifier or a model is asked the same thing:
which of these options fits this prompt best. A decider answers that question
and nothing else; :class:`~.decider_router.DeciderRouter` builds the question
from a :class:`~web_router.RouteRequest`, checks the reply and turns it into a
:class:`~web_router.RouteDecision`. A decider has ``decide(question)``,
``adecide(question)`` or both, and may set ``name``, ``timeout_s`` and
``route_tools`` as a router does.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from web_router.routing.contract import Budget, ToolName


@dataclass(frozen=True, kw_only=True)
class DeciderQuestion:
    """What a decider is asked: pick among ``options`` for ``prompt``."""

    prompt: str
    """The prompt the call routes: the query, the URLs for extract, the task for browse."""

    instructions: str
    """What the methods do, and whether the method is fixed."""

    options: Mapping[str, str]
    """Each option's id, a candidate id such as ``search:exa``, and its description: method, backend, est.
    cost and p90 latency. Extract is offered only when the prompt names a URL."""

    budget: Budget | None = None
    """The call's budget, when it has one."""

    pinned_tool: ToolName | None = None
    """The method the caller called, when fixed: rank its backends only."""

    deadline_s: float = 0.0
    """Seconds the decider has to answer."""


@dataclass(frozen=True, kw_only=True)
class DeciderReply:
    """A decider's answer: the options it ranks, best first. One it leaves out is not ranked: no opinion."""

    ranked: Sequence[str]
    """Option ids from the question, best first, each once."""

    scores: Mapping[str, float] | None = None
    """A number from 0 to 1 per ranked id, such as a probability; the first one's shows in the reason."""

    confidence: float | None = None
    """How sure the decider is, from 0 to 1."""


class SyncDecider(Protocol):
    """A decider that answers synchronously."""

    def decide(self, question: DeciderQuestion) -> DeciderReply: ...


class AsyncDecider(Protocol):
    """A decider that answers asynchronously."""

    async def adecide(self, question: DeciderQuestion) -> DeciderReply: ...


Decider = SyncDecider | AsyncDecider
"""What :class:`~.decider_router.DeciderRouter` takes: an object with ``decide``, ``adecide`` or both."""
