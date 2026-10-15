"""Route a query before spending on it: a decision model labels it, code picks the method and chain.

A spike, opt-in through ``WebRouter(decider="jev")``: see :meth:`~web_router.WebRouter.classify`.
:class:`JevDecider` needs ``httpx``, which a bare install lacks, so it loads on first use.
"""

from typing import Any

from web_router.decisions.route import Answer, Route, route_from_labels

__all__ = ["Answer", "JevDecider", "Route", "route_from_labels"]


def __getattr__(name: str) -> Any:
    if name == "JevDecider":
        from web_router.decisions.jev import JevDecider

        return JevDecider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
