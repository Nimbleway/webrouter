"""The twenty-one tools, and the session state they share.

Everything here is a thin projection of :class:`~web_router.AsyncWebRouter`:
each tool's arguments are the library's own keyword names, passed through
untranslated, and a failure is the library's own message. The two things this
layer decides for itself are which providers a tool will admit -- generated per
install from what is actually configured -- and how much of a response fits in
a context window, which is :mod:`web_router.mcp.shaping`'s job. The one tool
that is not a method, ``load_env``, changes what is configured: it loads keys
from a file the user names, through :mod:`web_router.mcp.env`.

The router is asynchronous so that ``research`` and ``browse`` can wait out
their budgets without holding up the pipe the client is pinging on, and so the
``browser_*`` tools drive :class:`~web_router.AsyncBrowserHandle` s -- Playwright's
sync API refuses to run inside the server's event loop. Those tools keep their
sessions in :mod:`web_router.mcp.browsers`, and address them by an id minted
there.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from typing import Any, get_args

from mcp_types import Tool

from web_router.client import AsyncWebRouter
from web_router.constants import (
    ANSWER_ENV_BY_PROVIDER,
    BROWSE_ENV_BY_PROVIDER,
    BROWSER_ENV_BY_PROVIDER,
    DELEGATING_PROVIDERS,
    ENV_BY_PROVIDER,
    ENV_MCP_ENV_FILE,
    EXTRACT_ENV_BY_PROVIDER,
    MCP_ARG_FORMAT,
    MCP_ARG_HANDLE,
    MCP_ARG_MAX_CHARS,
    MCP_ARG_PATH,
    MCP_ARG_PROVIDER,
    MCP_ARG_REF,
    MCP_ARG_REMEMBER,
    MCP_ARG_SELECTOR,
    MCP_ARG_SESSION_ID,
    MCP_ARG_STEALTH,
    MCP_BROWSE_WAIT,
    MCP_BROWSER_ACTION_TIMEOUT_LIMIT,
    MCP_BROWSER_END_CLOSED,
    MCP_BROWSER_SESSION_TIMEOUT_LIMIT,
    MCP_BROWSER_SNAPSHOT_MAX_CHARS,
    MCP_BROWSER_SNAPSHOT_MAX_CHARS_LIMIT,
    MCP_BROWSER_TOOLS,
    MCP_METHOD_BROWSER,
    MCP_METHOD_TEMPLATE,
    MCP_MSG_BROWSE_PENDING,
    MCP_MSG_BROWSER_ALREADY_ENDED,
    MCP_MSG_BROWSER_LOWERED,
    MCP_MSG_BROWSER_OPENED,
    MCP_MSG_BROWSER_UNDECLARED,
    MCP_MSG_ENV_BAD_ARGUMENTS,
    MCP_MSG_ENV_HINT,
    MCP_MSG_ENV_NOTHING_NEW,
    MCP_MSG_ENV_REMEMBERED,
    MCP_MSG_INSTRUCTIONS_NO_KEYS,
    MCP_MSG_MISSING_ARGUMENT,
    MCP_MSG_NO_BACKEND,
    MCP_MSG_NO_BACKEND_HINT,
    MCP_MSG_NO_KEYS,
    MCP_MSG_NO_RAW,
    MCP_MSG_NOT_A_JOB,
    MCP_MSG_RESEARCH_PENDING,
    MCP_MSG_SNAPSHOT_TRUNCATED,
    MCP_MSG_UNKNOWN_HANDLE,
    MCP_MSG_UNKNOWN_TOOL,
    MCP_PROVIDER_DESCRIPTION,
    MCP_RESEARCH_WAIT,
    MCP_SERVER_INSTRUCTIONS,
    MCP_TOOL_ANSWER,
    MCP_TOOL_BROWSE,
    MCP_TOOL_BROWSE_STATUS,
    MCP_TOOL_BROWSER_CLICK,
    MCP_TOOL_BROWSER_CLOSE,
    MCP_TOOL_BROWSER_GOTO,
    MCP_TOOL_BROWSER_OPEN,
    MCP_TOOL_BROWSER_PRESS,
    MCP_TOOL_BROWSER_READ,
    MCP_TOOL_BROWSER_SCREENSHOT,
    MCP_TOOL_BROWSER_SNAPSHOT,
    MCP_TOOL_BROWSER_TYPE,
    MCP_TOOL_DESCRIPTIONS,
    MCP_TOOL_EXTRACT,
    MCP_TOOL_FETCH_RAW,
    MCP_TOOL_LOAD_ENV,
    MCP_TOOL_PROPERTIES,
    MCP_TOOL_REQUIRED,
    MCP_TOOL_RESEARCH,
    MCP_TOOL_RESEARCH_STATUS,
    MCP_TOOL_RUN_TEMPLATE,
    MCP_TOOL_SEARCH,
    MCP_TOOL_TEMPLATE,
    MCP_TOOL_TEMPLATES,
    MCP_TOOL_WAITS,
    RESEARCH_ENV_BY_PROVIDER,
    TEMPLATE_ENV_BY_PROVIDER,
)
from web_router.exceptions import (
    BrowseTimeoutError,
    ProviderNotFoundError,
    ProviderNotInstalledError,
    ResearchTimeoutError,
)
from web_router.mcp.browsers import BrowserSessions, check_target, playwright_installed, require_playwright
from web_router.mcp.env import EnvSources, user_config_dir
from web_router.mcp.shaping import (
    HandleStore,
    Record,
    ToolOutput,
    max_response_chars,
    redact,
    redaction_note,
    shape_answer,
    shape_browse,
    shape_extract,
    shape_pending,
    shape_progress,
    shape_research,
    shape_search,
)
from web_router.mcp.shaping_templates import shape_template, shape_template_run, shape_templates
from web_router.providers.auto import AutoBrowserProvider, AutoExtractProvider, AutoProvider
from web_router.providers.base import BrowseJob, BrowserProvider, ResearchJob, TemplateProvider, _BaseProvider
from web_router.registry import (
    configured_browse_providers,
    configured_browser_providers,
    configured_extract_providers,
    configured_providers,
    configured_research_providers,
    configured_template_providers,
    get_browse_provider_class,
    get_browser_provider_class,
    get_extract_provider_class,
    get_provider_class,
    get_research_provider_class,
    get_template_provider_class,
)
from web_router.registry_answer import configured_answer_providers, get_answer_provider_class
from web_router.types import (
    BROWSE_PARAMETERS,
    BROWSER_PARAMETERS,
    EXTRACT_PARAMETERS,
    NORMALIZED_PARAMETERS,
    RESEARCH_PARAMETERS,
    TEMPLATE_PARAMETERS,
    ExtractFormat,
    ExtractResponse,
    PageState,
    StealthLevel,
)
from web_router.types_answer import ANSWER_PARAMETERS

_CONFIGURED: dict[str, Callable[[], list[str]]] = {
    MCP_TOOL_SEARCH: configured_providers,
    MCP_TOOL_EXTRACT: configured_extract_providers,
    MCP_TOOL_TEMPLATES: configured_template_providers,
    MCP_TOOL_TEMPLATE: configured_template_providers,
    MCP_TOOL_RUN_TEMPLATE: configured_template_providers,
    MCP_TOOL_ANSWER: configured_answer_providers,
    MCP_TOOL_RESEARCH: configured_research_providers,
    MCP_TOOL_BROWSE: configured_browse_providers,
    MCP_TOOL_BROWSER_OPEN: configured_browser_providers,
}
"""Which registry answers "what can this install actually serve?" for each tool.

Only the nine tools that reach a backend appear: the two ``*_status`` tools,
``fetch_raw`` and the browser actions work on what an earlier call left behind
and never pick a provider. The three template tools share one registry.
"""

_METHOD_OF: dict[str, str] = {
    MCP_TOOL_TEMPLATES: MCP_METHOD_TEMPLATE,
    MCP_TOOL_TEMPLATE: MCP_METHOD_TEMPLATE,
    MCP_TOOL_RUN_TEMPLATE: MCP_METHOD_TEMPLATE,
    MCP_TOOL_BROWSER_OPEN: MCP_METHOD_BROWSER,
}
"""The tools whose method the capabilities resource keys under another name; every other tool is its method's."""

_CLASS_OF: dict[str, Callable[[str], type[_BaseProvider]]] = {
    MCP_TOOL_SEARCH: get_provider_class,
    MCP_TOOL_EXTRACT: get_extract_provider_class,
    MCP_TOOL_TEMPLATES: get_template_provider_class,
    MCP_TOOL_TEMPLATE: get_template_provider_class,
    MCP_TOOL_RUN_TEMPLATE: get_template_provider_class,
    MCP_TOOL_ANSWER: get_answer_provider_class,
    MCP_TOOL_RESEARCH: get_research_provider_class,
    MCP_TOOL_BROWSE: get_browse_provider_class,
    MCP_TOOL_BROWSER_OPEN: get_browser_provider_class,
}
"""Each provider-picking tool's class lookup, which reads ``supported_parameters`` without a credential."""


def _search_chain(router: AsyncWebRouter, name: str) -> tuple[str, ...]:
    """The members the router's own search ``name`` would try, read as :func:`_browser_chain` reads its own."""
    provider = router.get_provider(name)
    return provider.members if isinstance(provider, AutoProvider) else ()


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


_CHAIN_OF: dict[str, Callable[[AsyncWebRouter, str], tuple[str, ...]]] = {
    MCP_TOOL_SEARCH: _search_chain,
    MCP_TOOL_EXTRACT: _extract_chain,
    MCP_TOOL_BROWSER_OPEN: _browser_chain,
}
"""The tools whose delegating providers declare what some member serves, and how each one's members are read.

A delegating provider's own declaration says nothing about what can be served
here -- search ``auto`` declares every filter Nimble maps, whether or not this
machine holds a Nimble key, and extract's ``max`` declares every option as
``auto`` does -- so each stands for the chain it would try instead. Research
``auto`` declares a fixed surface, and is read as it is.
"""

_OPTIONS: dict[str, frozenset[str]] = {
    MCP_TOOL_SEARCH: NORMALIZED_PARAMETERS,
    MCP_TOOL_EXTRACT: EXTRACT_PARAMETERS,
    # Listing and describing take no option a backend may decline.
    MCP_TOOL_TEMPLATES: frozenset(),
    MCP_TOOL_TEMPLATE: frozenset(),
    MCP_TOOL_RUN_TEMPLATE: TEMPLATE_PARAMETERS,
    MCP_TOOL_ANSWER: ANSWER_PARAMETERS,
    MCP_TOOL_RESEARCH: RESEARCH_PARAMETERS,
    MCP_TOOL_BROWSE: BROWSE_PARAMETERS,
    MCP_TOOL_BROWSER_OPEN: BROWSER_PARAMETERS,
}
"""The arguments a backend may or may not map, as opposed to the ones every backend takes."""

_ENV_TABLE: dict[str, dict[str, Any]] = {
    MCP_TOOL_SEARCH: ENV_BY_PROVIDER,
    MCP_TOOL_EXTRACT: EXTRACT_ENV_BY_PROVIDER,
    MCP_TOOL_TEMPLATES: TEMPLATE_ENV_BY_PROVIDER,
    MCP_TOOL_TEMPLATE: TEMPLATE_ENV_BY_PROVIDER,
    MCP_TOOL_RUN_TEMPLATE: TEMPLATE_ENV_BY_PROVIDER,
    MCP_TOOL_ANSWER: ANSWER_ENV_BY_PROVIDER,
    MCP_TOOL_RESEARCH: RESEARCH_ENV_BY_PROVIDER,
    MCP_TOOL_BROWSE: BROWSE_ENV_BY_PROVIDER,
    MCP_TOOL_BROWSER_OPEN: BROWSER_ENV_BY_PROVIDER,
}

_Handler = Callable[[Mapping[str, Any]], Awaitable["dict[str, Any] | ToolOutput"]]


class WebRouterTools:
    """One MCP session's tools: a router, and the handles its calls left behind."""

    def __init__(
        self,
        router: AsyncWebRouter | None = None,
        *,
        store: HandleStore | None = None,
        research_wait: float = MCP_RESEARCH_WAIT,
        browse_wait: float = MCP_BROWSE_WAIT,
        browsers: BrowserSessions | None = None,
        env: EnvSources | None = None,
        router_factory: Callable[[], AsyncWebRouter] = AsyncWebRouter,
    ) -> None:
        """
        Args:
            router: The router to serve calls with. Built by ``router_factory``
                when omitted, which is what reads the user's own environment
                for their own keys.
            store: Where handles are kept. Bounded; a fresh one per session.
            research_wait: Seconds ``research`` waits before handing back a
                handle instead of an answer.
            browse_wait: The same, for ``browse``.
            browsers: The session table the ``browser_*`` tools share. A fresh
                one per session, with the idle limit and cap from constants.
            env: Where the process's credentials came from, reported by the
                capabilities resource and added to by ``load_env``. The entry
                point passes what its startup lookup found.
            router_factory: Builds the router that takes over once
                ``load_env`` has configured a backend. The package defaults
                unless given; pass one whenever ``router`` is not those.
        """
        self._router_factory = router_factory
        self.router = router if router is not None else router_factory()
        # Routers load_env replaced: still serving every browser session and
        # job they started, and closed with the rest in aclose().
        self._retired: list[AsyncWebRouter] = []
        self._tools_changed = False
        self.store = store if store is not None else HandleStore()
        self.research_wait = research_wait
        self.browse_wait = browse_wait
        self.browsers = browsers if browsers is not None else BrowserSessions()
        self.env = env if env is not None else EnvSources()
        self._waits = {**MCP_TOOL_WAITS, MCP_TOOL_RESEARCH: research_wait, MCP_TOOL_BROWSE: browse_wait}
        self._handlers: dict[str, _Handler] = {
            MCP_TOOL_SEARCH: self._search,
            MCP_TOOL_EXTRACT: self._extract,
            MCP_TOOL_TEMPLATES: self._templates,
            MCP_TOOL_TEMPLATE: self._template,
            MCP_TOOL_RUN_TEMPLATE: self._run_template,
            MCP_TOOL_ANSWER: self._answer,
            MCP_TOOL_RESEARCH: self._research,
            MCP_TOOL_RESEARCH_STATUS: self._research_status,
            MCP_TOOL_BROWSE: self._browse,
            MCP_TOOL_BROWSE_STATUS: self._browse_status,
            MCP_TOOL_FETCH_RAW: self._fetch_raw,
            MCP_TOOL_LOAD_ENV: self._load_env,
            MCP_TOOL_BROWSER_OPEN: self._browser_open,
            MCP_TOOL_BROWSER_GOTO: self._browser_goto,
            MCP_TOOL_BROWSER_SNAPSHOT: self._browser_snapshot,
            MCP_TOOL_BROWSER_CLICK: self._browser_click,
            MCP_TOOL_BROWSER_TYPE: self._browser_type,
            MCP_TOOL_BROWSER_PRESS: self._browser_press,
            MCP_TOOL_BROWSER_SCREENSHOT: self._browser_screenshot,
            MCP_TOOL_BROWSER_READ: self._browser_read,
            MCP_TOOL_BROWSER_CLOSE: self._browser_close,
        }

    def list_tools(self) -> list[Tool]:
        """Every tool this install can serve, with an input schema built from what it is configured for.

        The ``provider`` enum names only backends that have their credentials
        here, so the model cannot pick one that is certain to throw, and a user
        holding one key is not shown thirteen they do not have. For the same
        reason the ``browser_*`` tools are left out when Playwright is not
        installed: every one of them would fail.
        """
        hidden = () if playwright_installed() else MCP_BROWSER_TOOLS
        return [
            Tool(
                name=name,
                description=self._description(name),
                input_schema=self._schema(name),
            )
            for name in self._handlers
            if name not in hidden
        ]

    def capabilities(self) -> dict[str, Any]:
        """What this install can serve, per method, and where its keys came from -- the body of the one resource.

        With no key at all, ``env`` also carries a hint saying how to add one,
        since a model reading an all-keyless list cannot otherwise tell a user
        with no keys from one whose keys did not reach the server.
        """
        env = self.env.report()
        if not self.keyed():
            env["hint"] = MCP_MSG_ENV_HINT.format(env_var=ENV_MCP_ENV_FILE, user_dir=user_config_dir())
        return {
            **_configured(),
            "default_provider": self.router.default_provider,
            "env": env,
            "unlock": _unlock(),
        }

    def keyed(self) -> bool:
        """Whether any backend is configured here that needed a credential to be."""
        return any(_keyed(tool) for tool in _CONFIGURED)

    def instructions(self) -> str:
        """The server's instructions, saying what the model can do about it when no key reached the server."""
        return MCP_SERVER_INSTRUCTIONS + ("" if self.keyed() else MCP_MSG_INSTRUCTIONS_NO_KEYS)

    def tools_changed(self) -> bool:
        """Whether the tool list changed since this was last asked -- true once, so the client is told once."""
        changed, self._tools_changed = self._tools_changed, False
        return changed

    async def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any] | ToolOutput:
        """Run one tool and return its shaped result -- a :class:`ToolOutput` for a snapshot or screenshot.

        Raises:
            ValueError: for a tool, argument or handle this server cannot make
                sense of.
            WebRouterError: whatever the library raised, message untouched.
        """
        handler = self._handlers.get(name)
        if handler is None:
            raise ValueError(MCP_MSG_UNKNOWN_TOOL.format(name=name, tools=", ".join(self._handlers)))
        return await handler(arguments)

    async def aclose(self) -> None:
        """Release every browser session still open, then close every provider every router built."""
        async with AsyncExitStack() as stack:
            # Pushed first, so they unwind after the table: each router releases,
            # again, any handle the table never took -- one whose open was
            # cancelled half way -- before the providers it needs are closed.
            for router in (self.router, *self._retired):
                stack.push_async_callback(router.aclose)
            stack.push_async_callback(self.browsers.aclose)

    async def _search(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        response = await self.router.search(**self._kwargs(MCP_TOOL_SEARCH, arguments))
        record = self.store.add(MCP_TOOL_SEARCH, raw=response.raw)
        return shape_search(response, record.handle, budget=max_response_chars())

    async def _extract(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        response = await self.router.extract(**self._kwargs(MCP_TOOL_EXTRACT, arguments))
        record = self.store.add(MCP_TOOL_EXTRACT, raw=response.raw)
        return shape_extract(response, record.handle, budget=max_response_chars())

    async def _templates(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        page = await self.router.templates(**self._kwargs(MCP_TOOL_TEMPLATES, arguments))
        record = self.store.add(MCP_TOOL_TEMPLATES, raw=page.raw)
        return shape_templates(page, record.handle, budget=max_response_chars())

    async def _template(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        template = await self.router.template(**self._kwargs(MCP_TOOL_TEMPLATE, arguments))
        record = self.store.add(MCP_TOOL_TEMPLATE, raw=template.raw)
        return shape_template(template, record.handle, budget=max_response_chars())

    async def _run_template(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        response = await self.router.run_template(**self._kwargs(MCP_TOOL_RUN_TEMPLATE, arguments))
        record = self.store.add(MCP_TOOL_RUN_TEMPLATE, raw=response.raw)
        return shape_template_run(response, record.handle, budget=max_response_chars())

    async def _answer(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        response = await self.router.answer(**self._kwargs(MCP_TOOL_ANSWER, arguments))
        record = self.store.add(MCP_TOOL_ANSWER, raw=response.raw)
        return shape_answer(response, record.handle, budget=max_response_chars())

    async def _research(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        job = await self.router.submit_research(**self._kwargs(MCP_TOOL_RESEARCH, arguments))
        try:
            response = await job.aresult(timeout=self.research_wait)
        except ResearchTimeoutError:
            # Not a failure: the run is still going, and a stdio session cannot
            # be held open for the ten minutes it may take.
            handle = self.store.add(MCP_TOOL_RESEARCH, job=job).handle
            return shape_pending(job, handle, wait=self.research_wait, note=MCP_MSG_RESEARCH_PENDING)
        record = self.store.add(MCP_TOOL_RESEARCH, raw=response.raw, job=job)
        return shape_research(response, record.handle, budget=max_response_chars())

    async def _research_status(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        record = self._record(MCP_TOOL_RESEARCH_STATUS, arguments)
        if not isinstance(record.job, ResearchJob):
            raise self._not_a_job(record, MCP_TOOL_RESEARCH_STATUS, MCP_TOOL_RESEARCH)
        progress = await record.job.astatus()
        if progress.response is None:
            return shape_progress(progress, record.handle)
        record.raw = progress.response.raw
        return shape_research(progress.response, record.handle, budget=max_response_chars())

    async def _browse(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        job = await self.router.submit_browse(**self._kwargs(MCP_TOOL_BROWSE, arguments))
        try:
            response = await job.aresult(timeout=self.browse_wait)
        except BrowseTimeoutError:
            # As for research: the task is still going, and is polled rather than waited on.
            handle = self.store.add(MCP_TOOL_BROWSE, job=job).handle
            return shape_pending(job, handle, wait=self.browse_wait, note=MCP_MSG_BROWSE_PENDING)
        record = self.store.add(MCP_TOOL_BROWSE, raw=response.raw, job=job)
        return shape_browse(response, record.handle, budget=max_response_chars())

    async def _browse_status(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        record = self._record(MCP_TOOL_BROWSE_STATUS, arguments)
        if not isinstance(record.job, BrowseJob):
            raise self._not_a_job(record, MCP_TOOL_BROWSE_STATUS, MCP_TOOL_BROWSE)
        progress = await record.job.astatus()
        if progress.response is None:
            # A failed task's detail is the backend's own message, which can quote a session URL.
            shaped, count = redact(shape_progress(progress, record.handle))
            if count:
                shaped["note"] = redaction_note(count).strip()
            return dict(shaped)
        record.raw = progress.response.raw
        return shape_browse(progress.response, record.handle, budget=max_response_chars())

    async def _fetch_raw(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        record = self._record(MCP_TOOL_FETCH_RAW, arguments)
        if record.raw is None:
            raise ValueError(MCP_MSG_NO_RAW.format(handle=record.handle))
        if not isinstance(record.job, BrowseJob) and record.tool not in MCP_BROWSER_TOOLS:
            # Deliberately unbounded: this tool exists to hand back exactly what
            # the shaped result could not carry.
            return {"handle": record.handle, "tool": record.tool, "raw": record.raw}
        # A browse payload is the one that is not handed back verbatim: it can
        # carry the session the agent ran in, and a live view or a key-bearing
        # CDP URL is control of a billed browser, not data about the task.
        raw, count = redact(record.raw)
        payload: dict[str, Any] = {"handle": record.handle, "tool": record.tool, "raw": raw}
        if count:
            payload["note"] = redaction_note(count).strip()
        return payload

    async def _load_env(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        kwargs = self._kwargs(MCP_TOOL_LOAD_ENV, arguments)
        path, remember = kwargs[MCP_ARG_PATH], kwargs.get(MCP_ARG_REMEMBER, False)
        if not isinstance(path, str) or not isinstance(remember, bool):
            raise ValueError(MCP_MSG_ENV_BAD_ARGUMENTS)
        applied = self.env.load(path, remember=remember)
        if applied.loaded:
            self._rebuild()
        payload: dict[str, Any] = {"file": str(applied.path), "loaded": list(applied.loaded)}
        if applied.already_set:
            payload["already_set"] = list(applied.already_set)
        payload["configured"] = _configured()
        notes = [] if applied.loaded else [MCP_MSG_ENV_NOTHING_NEW]
        if applied.remembered is not None:
            notes.append(MCP_MSG_ENV_REMEMBERED.format(pointer=applied.remembered))
        if notes:
            payload["note"] = " ".join(notes)
        return payload

    def _rebuild(self) -> None:
        """Hand every later call to a router built from the environment as it is now.

        A router resolves each ``auto`` chain once, when it first builds it, and
        keeps every provider it built -- a keyless ``jina`` would never see a key
        loaded after it -- so a key loaded at runtime needs a new one. The old
        one is kept rather than closed: every browser session it opened and
        every job still being polled runs on its providers.
        """
        self._retired.append(self.router)
        self.router = self._router_factory()
        self._tools_changed = True

    async def _browser_open(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        # Refused rather than dropped, unlike elsewhere: the undeclared argument
        # a model is likeliest to send here is `provider_options`, and it should
        # learn that it cannot, not that it silently did not.
        allowed = [*MCP_TOOL_PROPERTIES[MCP_TOOL_BROWSER_OPEN], MCP_ARG_PROVIDER]
        undeclared = sorted(name for name in arguments if name not in allowed)
        if undeclared:
            raise ValueError(MCP_MSG_BROWSER_UNDECLARED.format(names=", ".join(undeclared), allowed=", ".join(allowed)))
        kwargs = self._kwargs(MCP_TOOL_BROWSER_OPEN, arguments)
        # Sent unset, and never past the library's own default, so a session a
        # conversation forgets still ends on the backend's side whatever happens
        # to this process -- but only to a backend that maps a lifetime, or a
        # custom one without it would refuse every open.
        backend = self.router.get_browser_provider(kwargs.get(MCP_ARG_PROVIDER))
        if "timeout" in type(backend).supported_parameters:
            kwargs.setdefault("timeout", MCP_BROWSER_SESSION_TIMEOUT_LIMIT)
        lowered: list[str] = []
        for name in ("timeout", "idle_timeout"):
            _lower(kwargs, name, MCP_BROWSER_SESSION_TIMEOUT_LIMIT, lowered)
        require_playwright()
        session_id = await self.browsers.open(lambda: self.router.open_browser(**kwargs))
        return {
            MCP_ARG_SESSION_ID: session_id,
            "provider": self.browsers.provider_of(session_id),
            "state": "open",
            "note": _noted(MCP_MSG_BROWSER_OPENED.format(idle=self.browsers.idle_timeout), lowered),
        }

    async def _browser_goto(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        session_id, kwargs, lowered = self._session_kwargs(MCP_TOOL_BROWSER_GOTO, arguments)
        self.browsers.check_url(session_id, MCP_TOOL_BROWSER_GOTO, str(kwargs["url"]))
        state = await self.browsers.run(session_id, MCP_TOOL_BROWSER_GOTO, lambda handle: handle.goto(**kwargs))
        return self._page_state(session_id, state, lowered)

    async def _browser_snapshot(self, arguments: Mapping[str, Any]) -> ToolOutput:
        session_id, kwargs, lowered = self._session_kwargs(MCP_TOOL_BROWSER_SNAPSHOT, arguments)
        kwargs.setdefault(MCP_ARG_MAX_CHARS, MCP_BROWSER_SNAPSHOT_MAX_CHARS)
        _lower(kwargs, MCP_ARG_MAX_CHARS, MCP_BROWSER_SNAPSHOT_MAX_CHARS_LIMIT, lowered)
        snapshot = await self.browsers.run(
            session_id, MCP_TOOL_BROWSER_SNAPSHOT, lambda handle: handle.snapshot(**kwargs)
        )
        tree, tree_count = self.browsers.scrub(snapshot.tree)
        payload: dict[str, Any] = {
            MCP_ARG_SESSION_ID: session_id,
            "url": snapshot.url,
            "title": snapshot.title,
            "truncated": snapshot.truncated,
        }
        if snapshot.truncated:
            payload["note"] = MCP_MSG_SNAPSHOT_TRUNCATED.format(max_chars=kwargs[MCP_ARG_MAX_CHARS])
        return ToolOutput(payload=self._scrubbed(payload, tree_count, lowered), text=tree)

    async def _browser_click(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        session_id, kwargs, lowered = self._session_kwargs(MCP_TOOL_BROWSER_CLICK, arguments)
        check_target(MCP_TOOL_BROWSER_CLICK, kwargs.get(MCP_ARG_REF), kwargs.get(MCP_ARG_SELECTOR))
        state = await self.browsers.run(session_id, MCP_TOOL_BROWSER_CLICK, lambda handle: handle.click(**kwargs))
        return self._page_state(session_id, state, lowered)

    async def _browser_type(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        session_id, kwargs, lowered = self._session_kwargs(MCP_TOOL_BROWSER_TYPE, arguments)
        check_target(MCP_TOOL_BROWSER_TYPE, kwargs.get(MCP_ARG_REF), kwargs.get(MCP_ARG_SELECTOR))
        text = kwargs.pop("text")
        state = await self.browsers.run(session_id, MCP_TOOL_BROWSER_TYPE, lambda handle: handle.type(text, **kwargs))
        return self._page_state(session_id, state, lowered)

    async def _browser_press(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        session_id, kwargs, lowered = self._session_kwargs(MCP_TOOL_BROWSER_PRESS, arguments)
        state = await self.browsers.run(session_id, MCP_TOOL_BROWSER_PRESS, lambda handle: handle.press(**kwargs))
        return self._page_state(session_id, state, lowered)

    async def _browser_screenshot(self, arguments: Mapping[str, Any]) -> ToolOutput:
        session_id, kwargs, lowered = self._session_kwargs(MCP_TOOL_BROWSER_SCREENSHOT, arguments)
        shot = await self.browsers.run(
            session_id, MCP_TOOL_BROWSER_SCREENSHOT, lambda handle: handle.screenshot(**kwargs)
        )
        payload = {MCP_ARG_SESSION_ID: session_id, "url": shot.url, "width": shot.width, "height": shot.height}
        return ToolOutput(payload=self._scrubbed(payload, lowered=lowered), png=shot.png)

    async def _browser_read(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        session_id, kwargs, lowered = self._session_kwargs(MCP_TOOL_BROWSER_READ, arguments)
        document = await self.browsers.run(session_id, MCP_TOOL_BROWSER_READ, lambda handle: handle.read(**kwargs))
        # Scrubbed before it is stored, so `fetch_raw` on this handle can only hand back what is safe.
        raw, count = self.browsers.scrub(document.model_dump(mode="json"))
        response = ExtractResponse(
            provider=self.browsers.provider_of(session_id), documents=[type(document).model_validate(raw)], raw=[raw]
        )
        record = self.store.add(MCP_TOOL_BROWSER_READ, raw=raw)
        shaped = shape_extract(response, record.handle, budget=max_response_chars())
        return {MCP_ARG_SESSION_ID: session_id, **self._scrubbed(shaped, count, lowered)}

    async def _browser_close(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        session_id = str(self._kwargs(MCP_TOOL_BROWSER_CLOSE, arguments)[MCP_ARG_SESSION_ID])
        ended = await self.browsers.close(session_id, MCP_BROWSER_END_CLOSED)
        payload: dict[str, Any] = {MCP_ARG_SESSION_ID: session_id, "state": "released"}
        if ended is not None:
            payload["note"] = MCP_MSG_BROWSER_ALREADY_ENDED.format(reason=ended)
        return payload

    def _session_kwargs(self, tool: str, arguments: Mapping[str, Any]) -> tuple[str, dict[str, Any], list[str]]:
        """The session id, the action's keyword arguments, and a note for each one lowered to its limit."""
        kwargs = self._kwargs(tool, arguments)
        lowered: list[str] = []
        # An action holds its session's lock, and a held session is never reaped.
        _lower(kwargs, "timeout", MCP_BROWSER_ACTION_TIMEOUT_LIMIT, lowered)
        return str(kwargs.pop(MCP_ARG_SESSION_ID)), kwargs, lowered

    def _page_state(self, session_id: str, state: PageState, lowered: list[str]) -> dict[str, Any]:
        return self._scrubbed({MCP_ARG_SESSION_ID: session_id, **state.model_dump()}, lowered=lowered)

    def _scrubbed(self, payload: dict[str, Any], already: int = 0, lowered: list[str] | None = None) -> dict[str, Any]:
        """``payload`` with every session value redacted, and a note saying how many were and what was lowered.

        The server's own ``session_id`` is left out of the scrub: it is the
        handle the model needs for its next call, and the redactor would take
        it for a backend's session id by its name.
        """
        own = {name: value for name, value in payload.items() if name == MCP_ARG_SESSION_ID}
        scrubbed, count = self.browsers.scrub({name: value for name, value in payload.items() if name not in own})
        cleaned = {**own, **scrubbed}
        note = _noted(redaction_note(count + already).strip(), lowered or [])
        if note:
            cleaned["note"] = f"{cleaned['note']} {note}" if cleaned.get("note") else note
        return dict(cleaned)

    @staticmethod
    def _not_a_job(record: Record, poller: str, job_tool: str) -> ValueError:
        return ValueError(
            MCP_MSG_NOT_A_JOB.format(handle=record.handle, tool=record.tool, poller=poller, job_tool=job_tool)
        )

    def _record(self, tool: str, arguments: Mapping[str, Any]) -> Record:
        handle = str(self._kwargs(tool, arguments)[MCP_ARG_HANDLE])
        record = self.store.get(handle)
        if record is None:
            raise ValueError(MCP_MSG_UNKNOWN_HANDLE.format(handle=handle, limit=self.store.limit))
        return record

    def _kwargs(self, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """The library call's keyword arguments, taken straight from the tool's own.

        The tool's parameter names *are* the library's, so there is no mapping
        table to drift: an argument the schema does not declare is dropped, and
        one left unset stays unset rather than being sent as a default of ours.
        """
        names = list(MCP_TOOL_PROPERTIES[tool])
        if tool in _CONFIGURED:
            names.append(MCP_ARG_PROVIDER)
        # `is None`, not truthiness: browser_type's required `text` may be ""
        # -- the way a field is cleared -- and supplied-but-empty is not missing.
        missing = [name for name in MCP_TOOL_REQUIRED[tool] if arguments.get(name) is None]
        if missing:
            raise ValueError(MCP_MSG_MISSING_ARGUMENT.format(tool=tool, name=missing[0]))
        return {name: arguments[name] for name in names if arguments.get(name) is not None}

    def _description(self, tool: str) -> str:
        """The tool's description, plus a warning when nothing can serve it here, or no key reached the server."""
        description = MCP_TOOL_DESCRIPTIONS[tool]
        if tool in self._waits:
            description = description.format(wait=self._waits[tool])
        elif tool == MCP_TOOL_BROWSER_OPEN:
            description = description.format(idle=self.browsers.idle_timeout, max_sessions=self.browsers.max_sessions)
        elif tool == MCP_TOOL_BROWSER_SNAPSHOT:
            description = description.format(max_chars=MCP_BROWSER_SNAPSHOT_MAX_CHARS)
        if tool in _CONFIGURED and not _usable(tool):
            description += MCP_MSG_NO_BACKEND
            env_vars = _env_vars(tool)
            if env_vars:
                description += MCP_MSG_NO_BACKEND_HINT.format(env_vars=", ".join(env_vars))
        if tool == MCP_TOOL_SEARCH and not self.keyed():
            # On search alone: it still works keyless, and it is the tool a model reaches for first.
            description += MCP_MSG_NO_KEYS
        return description

    def _schema(self, tool: str) -> dict[str, Any]:
        """The tool's input schema, offering only what some backend reachable from here maps.

        An option no configured backend and not the router's default declares
        would fail on every route the model can pick, so it is left out rather
        than advertised; so is a stealth level none of them serves. A search,
        extract or browser ``auto`` counts as the members it would try, because
        it declares what any member maps. A model that sends one anyway still reaches the
        library and its :class:`~web_router.exceptions.UnsupportedParameterError`,
        never a silent drop.
        """
        properties: dict[str, Any] = dict(MCP_TOOL_PROPERTIES[tool])
        providers = _CONFIGURED[tool]() if tool in _CONFIGURED else []
        if tool in _CONFIGURED:
            serving = _serving(tool, [*providers, self.router.default_provider], self.router)
            mapped = frozenset().union(*(cls.supported_parameters for cls in serving))
            properties = {name: spec for name, spec in properties.items() if name not in _OPTIONS[tool] - mapped}
            if MCP_ARG_STEALTH in properties and tool == MCP_TOOL_BROWSER_OPEN:
                _offer_levels(properties, serving)
            if MCP_ARG_FORMAT in properties and tool == MCP_TOOL_RUN_TEMPLATE:
                _offer_formats(properties, serving)
        if providers:
            properties[MCP_ARG_PROVIDER] = {
                "type": "string",
                "enum": providers,
                "description": MCP_PROVIDER_DESCRIPTION,
            }
        return {
            "type": "object",
            "properties": properties,
            # A required `provider` is listed only when there is one to offer; with none the tool is dead anyway.
            "required": [name for name in MCP_TOOL_REQUIRED[tool] if name in properties],
            "additionalProperties": False,
        }


def _serving(tool: str, providers: list[str], router: AsyncWebRouter) -> list[type[_BaseProvider]]:
    """The classes that would serve ``providers`` for this tool's method: each one's own, a delegating one's chain.

    A name this method does not know, or whose extra is not installed, can
    serve nothing, so it adds nothing.
    """
    names: list[str] = []
    for provider in providers:
        if provider in DELEGATING_PROVIDERS and tool in _CHAIN_OF:
            # Not every delegating name is every method's: ``max`` is extract's alone.
            try:
                names.extend(_CHAIN_OF[tool](router, provider))
            except (ProviderNotFoundError, ProviderNotInstalledError):
                continue
        else:
            names.append(provider)
    classes: list[type[_BaseProvider]] = []
    for name in dict.fromkeys(names):
        try:
            classes.append(_CLASS_OF[tool](name))
        except (ProviderNotFoundError, ProviderNotInstalledError):
            continue
    return classes


def _offer_levels(properties: dict[str, Any], serving: list[type[_BaseProvider]]) -> None:
    """Narrow ``stealth`` to the levels some backend serving here has, as the options are narrowed.

    A level none of them serves would be refused on every route, so it is not
    offered; with none at all the option goes too.
    """
    served = frozenset().union(*(cls.stealth_levels for cls in serving if issubclass(cls, BrowserProvider)))
    levels = [level for level in get_args(StealthLevel) if level in served]
    if levels:
        properties[MCP_ARG_STEALTH] = {**properties[MCP_ARG_STEALTH], "enum": levels}
    else:
        del properties[MCP_ARG_STEALTH]


def _offer_formats(properties: dict[str, Any], serving: list[type[_BaseProvider]]) -> None:
    """Narrow a template run's ``format`` to the page formats some backend serving here returns.

    Every template backend maps the option; what differs is which formats it
    can return beside the record. A format none of them returns would be
    refused on every route, so it is not offered, and with none at all the
    option goes too -- as :func:`_offer_levels` narrows ``stealth``.
    """
    served = frozenset().union(*(cls.supported_formats for cls in serving if issubclass(cls, TemplateProvider)))
    formats = [body_format for body_format in get_args(ExtractFormat) if body_format in served]
    if formats:
        properties[MCP_ARG_FORMAT] = {**properties[MCP_ARG_FORMAT], "enum": formats}
    else:
        del properties[MCP_ARG_FORMAT]


def _lower(kwargs: dict[str, Any], name: str, limit: float, lowered: list[str]) -> None:
    """Lower ``kwargs[name]`` to ``limit`` when it is a number past it, and say so in ``lowered``.

    Anything that is not a number is left for the library's validation to refuse.
    """
    value = kwargs.get(name)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > limit:
        kwargs[name] = type(value)(limit)
        lowered.append(MCP_MSG_BROWSER_LOWERED.format(name=name, value=value, limit=limit))


def _noted(note: str, lowered: list[str]) -> str:
    return " ".join(part for part in (note, *lowered) if part)


def _configured() -> dict[str, list[str]]:
    """The backends configured here, per method, keyed as the capabilities resource names them."""
    return {
        MCP_TOOL_SEARCH: configured_providers(),
        MCP_TOOL_EXTRACT: configured_extract_providers(),
        MCP_METHOD_TEMPLATE: configured_template_providers(),
        MCP_TOOL_ANSWER: configured_answer_providers(),
        MCP_TOOL_RESEARCH: configured_research_providers(),
        MCP_TOOL_BROWSE: configured_browse_providers(),
        MCP_METHOD_BROWSER: configured_browser_providers(),
    }


def _unlock() -> dict[str, dict[str, list[str]]]:
    """Per method, the backends no key has reached yet and the variables that would add each -- names only."""
    unlock: dict[str, dict[str, list[str]]] = {}
    for tool, table in _ENV_TABLE.items():
        # Keyed as the resource's own lists are, once per method: the three
        # template tools share one, and the browser tools' is "browser".
        method = _METHOD_OF.get(tool, tool)
        if method in unlock:
            continue
        configured = set(_CONFIGURED[tool]())
        missing = {
            provider: [name for name in ((env,) if isinstance(env, str) else env) if not os.environ.get(name)]
            for provider, env in table.items()
            if env and provider not in configured
        }
        if missing:
            unlock[method] = missing
    return unlock


def _keyed(tool: str) -> list[str]:
    """The backends configured for this tool's method that needed a credential to be."""
    table = _ENV_TABLE[tool]
    return [provider for provider in _CONFIGURED[tool]() if table.get(provider)]


def _usable(tool: str) -> bool:
    """Whether anything but a delegating provider is configured for this method.

    ``auto`` and ``max`` are always "configured" because they need no credential
    of their own -- they need whatever their chain does. For search and extract
    that chain ends in a keyless backend and always works; for research and
    browse every backend bills, so ``auto`` alone means nothing here can
    actually run. Answer has no ``auto``, so it is usable exactly when a key is there.
    """
    return any(provider not in DELEGATING_PROVIDERS for provider in _CONFIGURED[tool]())


def _env_vars(tool: str) -> list[str]:
    """Every variable that would configure a backend for this method."""
    table = _ENV_TABLE[tool]
    names: set[str] = set()
    for value in table.values():
        if isinstance(value, str):
            names.add(value)
        elif value is not None:
            names.update(value)
    return sorted(names)
