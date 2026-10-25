"""Route a query before spending on it: a decision model labels it, code picks the method and chain.

A spike, opt-in through ``WebRouter(decider=...)``: see :meth:`~web_router.WebRouter.classify`.
The decider is swappable -- ``"keywords"``, ``"jev"``, ``"llm"``, a name given
to :func:`register_decider`, or any :class:`DecisionProvider` instance.
Everything past the route types loads on first use,
so a bare install needs neither ``httpx`` nor ``openai``.
"""

from importlib import import_module
from typing import Any

from web_router.decisions.route import Answer, Route, route_from_labels

_LAZY = {
    "JevDecider": "web_router.decisions.jev",
    "LLMDecider": "web_router.decisions.llm",
    "KeywordsDecider": "web_router.decisions.rules",
    "DecisionProvider": "web_router.decisions.base",
    "available_deciders": "web_router.decisions.registry",
    "create_decider": "web_router.decisions.registry",
    "register_decider": "web_router.decisions.registry",
    "unregister_decider": "web_router.decisions.registry",
}

__all__ = ["Answer", "Route", "route_from_labels", *_LAZY]


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        return getattr(import_module(_LAZY[name]), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
