"""Route a query before spending on it: a decision model labels it, code picks the method and chain.

A spike, opt-in through ``WebRouter(decider=...)``: see :meth:`~web_router.WebRouter.classify`.
The decider is swappable -- ``"jev"``, ``"openai"``, ``"rules"``, a name given
to :func:`register_decider`, or any :class:`DecisionProvider` instance -- and
:class:`ValidatedDecider` checks its answers against the locked
:class:`Decision` schema. Everything past the route types loads on first use,
so a bare install needs neither ``httpx`` nor ``openai``.
"""

from importlib import import_module
from typing import Any

from web_router.decisions.route import Answer, Route, route_from_labels

_LAZY = {
    "JevDecider": "web_router.decisions.jev",
    "OpenAIDecider": "web_router.decisions.openai",
    "RulesDecider": "web_router.decisions.rules",
    "DecisionProvider": "web_router.decisions.base",
    "ValidatedDecider": "web_router.decisions.base",
    "conformance_violations": "web_router.decisions.base",
    "Decision": "web_router.decisions.schema",
    "DecisionLabels": "web_router.decisions.schema",
    "DecisionRoute": "web_router.decisions.schema",
    "SCHEMA_VERSION": "web_router.decisions.schema",
    "Violation": "web_router.decisions.schema",
    "validate_answers": "web_router.decisions.schema",
    "validate_decision": "web_router.decisions.schema",
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
