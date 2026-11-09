"""The members a delegating provider would try, per tool: what the tool schemas and the budget read ``auto`` as.

Read from the instances the session's router serves each method with, so a
``chain`` in ``provider_config`` is honoured exactly as a request honours it.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from web_router.client import AsyncWebRouter
from web_router.constants import MCP_TOOL_ANSWER, MCP_TOOL_BROWSER_OPEN, MCP_TOOL_EXTRACT, MCP_TOOL_SEARCH
from web_router.providers.auto import AutoBrowserProvider, AutoExtractProvider, AutoProvider
from web_router.providers.auto_answer import AutoAnswerProvider


def _members(getter: str, auto: type[Any], router: AsyncWebRouter, name: str) -> tuple[str, ...]:
    """The members the router's own ``name`` would try, read from the instance ``getter`` serves the method with.

    So a ``chain`` in ``provider_config`` is honoured exactly as a request
    honours it. Building it costs no credential: members are created only when
    tried. A backend that is no ``auto`` has none; extract's ``max`` subclasses
    ``auto``, so its members are the ones it would race.
    """
    provider = getattr(router, getter)(name)
    return tuple(provider.members) if isinstance(provider, auto) else ()


CHAIN_OF: dict[str, Callable[[AsyncWebRouter, str], tuple[str, ...]]] = {
    MCP_TOOL_SEARCH: functools.partial(_members, "get_provider", AutoProvider),
    MCP_TOOL_EXTRACT: functools.partial(_members, "get_extract_provider", AutoExtractProvider),
    MCP_TOOL_ANSWER: functools.partial(_members, "get_answer_provider", AutoAnswerProvider),
    MCP_TOOL_BROWSER_OPEN: functools.partial(_members, "get_browser_provider", AutoBrowserProvider),
}
"""The tools whose delegating providers declare what some member serves, and how each one's members are read.

A delegating provider's own declaration says nothing about what can be served
here -- search ``auto`` declares every filter Nimble maps, whether or not this
machine holds a Nimble key, and extract's ``max`` declares every option as
``auto`` does -- so each stands for the chain it would try instead. Research
``auto`` declares a fixed surface, and is read as it is.
"""
