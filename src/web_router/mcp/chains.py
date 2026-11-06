"""The members a delegating provider would try, per tool: what the tool schemas and the budget read ``auto`` as.

Read from the instances the session's router serves each method with, so a
``chain`` in ``provider_config`` is honoured exactly as a request honours it.
"""

from __future__ import annotations

from collections.abc import Callable

from web_router.client import AsyncWebRouter
from web_router.constants import MCP_TOOL_ANSWER, MCP_TOOL_BROWSER_OPEN, MCP_TOOL_EXTRACT, MCP_TOOL_SEARCH
from web_router.providers.auto import AutoBrowserProvider, AutoExtractProvider, AutoProvider
from web_router.providers.auto_answer import AutoAnswerProvider


def _search_chain(router: AsyncWebRouter, name: str) -> tuple[str, ...]:
    """The members the router's own search ``name`` would try, read as :func:`_browser_chain` reads its own."""
    provider = router.get_provider(name)
    return provider.members if isinstance(provider, AutoProvider) else ()


def _answer_chain(router: AsyncWebRouter, name: str) -> tuple[str, ...]:
    """The members the router's own answer ``name`` would try, read as :func:`_search_chain` reads its own."""
    provider = router.get_answer_provider(name)
    return provider.members if isinstance(provider, AutoAnswerProvider) else ()


def _extract_chain(router: AsyncWebRouter, name: str) -> tuple[str, ...]:
    """The members the router's own extract ``name`` would try, read as :func:`_browser_chain` reads its own.

    ``max`` subclasses ``auto``, so this reads the members it would race the
    same way.
    """
    provider = router.get_extract_provider(name)
    return provider.members if isinstance(provider, AutoExtractProvider) else ()


def _browser_chain(router: AsyncWebRouter, name: str) -> tuple[str, ...]:
    """The members the router's own browser ``name`` would try.

    Read from the instance the router serves ``open_browser`` with, so a
    ``chain`` in ``provider_config`` is honoured exactly as a request honours
    it. Building it costs no credential: members are created only when tried.
    """
    provider = router.get_browser_provider(name)
    return provider.members if isinstance(provider, AutoBrowserProvider) else ()


CHAIN_OF: dict[str, Callable[[AsyncWebRouter, str], tuple[str, ...]]] = {
    MCP_TOOL_SEARCH: _search_chain,
    MCP_TOOL_EXTRACT: _extract_chain,
    MCP_TOOL_ANSWER: _answer_chain,
    MCP_TOOL_BROWSER_OPEN: _browser_chain,
}
"""The tools whose delegating providers declare what some member serves, and how each one's members are read.

A delegating provider's own declaration says nothing about what can be served
here -- search ``auto`` declares every filter Nimble maps, whether or not this
machine holds a Nimble key, and extract's ``max`` declares every option as
``auto`` does -- so each stands for the chain it would try instead. Research
``auto`` declares a fixed surface, and is read as it is.
"""
