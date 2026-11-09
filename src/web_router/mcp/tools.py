"""The twenty-four tools, and the session state they share.

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

import functools
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from typing import Any

from mcp_types import Tool

from web_router.client import AsyncWebRouter
from web_router.constants import (
    ENV_MCP_ENV_FILE,
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
    MCP_MSG_BROWSE_PENDING,
    MCP_MSG_BROWSER_ALREADY_ENDED,
    MCP_MSG_BROWSER_LOWERED,
    MCP_MSG_BROWSER_OPENED,
    MCP_MSG_ENV_BAD_ARGUMENTS,
    MCP_MSG_ENV_HINT,
    MCP_MSG_ENV_NOTHING_NEW,
    MCP_MSG_ENV_REMEMBERED,
    MCP_MSG_INSTRUCTIONS_NO_KEYS,
    MCP_MSG_MISSING_ARGUMENT,
    MCP_MSG_NO_BACKEND,
    MCP_MSG_NO_BACKEND_HINT,
    MCP_MSG_NO_RAW,
    MCP_MSG_NOT_A_JOB,
    MCP_MSG_RESEARCH_PENDING,
    MCP_MSG_SNAPSHOT_TRUNCATED,
    MCP_MSG_UNKNOWN_HANDLE,
    MCP_MSG_UNKNOWN_TOOL,
    MCP_PROVIDER_DESCRIPTION,
    MCP_RESEARCH_WAIT,
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
)
from web_router.constants_mcp import (
    MCP_MSG_NOTHING_CONFIGURED,
    MCP_MSG_UNKNOWN_ARGUMENTS,
    MCP_SERVER_INSTRUCTIONS,
    MCP_TOOL_HINTS,
)
from web_router.exceptions import (
    BrowseTimeoutError,
    ResearchTimeoutError,
)
from web_router.mcp import methods
from web_router.mcp.browsers import BrowserSessions, check_target, playwright_installed, require_playwright
from web_router.mcp.budget import ConnectionBudget, browser_charged, template_charged, wait_left
from web_router.mcp.env import EnvSources, user_config_dir
from web_router.mcp.methods import METHODS
from web_router.mcp.routing import ROUTING_TOOLS
from web_router.mcp.routing_config import McpRouting
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
from web_router.mcp.urls import screen, url_values
from web_router.providers.base import BrowseJob, ResearchJob
from web_router.types import (
    ExtractResponse,
)
from web_router.types_page import PageState

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
        router_factory: Callable[[], AsyncWebRouter] | None = None,
        routing: McpRouting | None = None,
    ) -> None:
        """
        Args:
            router: The router to serve calls with. Built by ``router_factory`` when omitted, which
                is what reads the user's own environment for their own keys.
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
                ``load_env`` has configured a backend. The package defaults, with
                ``routing``'s router attached, unless given.
            routing: The operator's router and ceiling (:mod:`web_router.mcp.routing_config`), read
                from the process environment when omitted. The ceiling bounds :attr:`budget`.
        """
        self.routing = routing if routing is not None else McpRouting.from_environ()
        self.budget = ConnectionBudget(self.routing.ceiling)
        self._router_factory = router_factory or self.routing.build
        self.router = router if router is not None else self._router_factory()
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
            **{name: functools.partial(handler, self) for name, handler in ROUTING_TOOLS.items()},
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
                title=MCP_TOOL_HINTS[name][0],
                description=self._description(name),
                input_schema=self._schema(name),
                annotations=methods.tool_annotations(name),
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
            **methods.configured(),
            "default_provider": self.router.default_provider,
            "env": env,
            "unlock": methods.unlock(),
            "routing": self.capabilities_routing(),
        }

    def capabilities_routing(self) -> dict[str, Any]:
        """The resource's ``routing`` section: the router, where prompts go, ``url_policy``, ceiling and budget."""
        return self.routing.capabilities(self.budget.status())

    def keyed(self) -> bool:
        """Whether any backend is configured here that needed a credential to be."""
        return any(methods.keyed(method) for method in methods.EACH_METHOD)

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
        if unknown := sorted(set(arguments) - set(allowed := methods.argument_names(name))):
            names, allowed_text = ", ".join(unknown), ", ".join(allowed) or "no arguments"
            raise ValueError(MCP_MSG_UNKNOWN_ARGUMENTS.format(tool=name, names=names, allowed=allowed_text))
        with self.budget.bound():
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
        response = await self.router.search(**self.arguments(MCP_TOOL_SEARCH, arguments))
        record = self.store.add(MCP_TOOL_SEARCH, raw=response.raw)
        return shape_search(response, record.handle, budget=max_response_chars())

    async def _extract(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        kwargs = self.arguments(MCP_TOOL_EXTRACT, arguments)
        screen(MCP_TOOL_EXTRACT, kwargs["urls"])
        response = await self.router.extract(**kwargs)
        record = self.store.add(MCP_TOOL_EXTRACT, raw=response.raw)
        return shape_extract(response, record.handle, budget=max_response_chars())

    async def _templates(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        page = await self.router.templates(**self.arguments(MCP_TOOL_TEMPLATES, arguments))
        record = self.store.add(MCP_TOOL_TEMPLATES, raw=page.raw)
        return shape_templates(page, record.handle, budget=max_response_chars())

    async def _template(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        template = await self.router.template(**self.arguments(MCP_TOOL_TEMPLATE, arguments))
        record = self.store.add(MCP_TOOL_TEMPLATE, raw=template.raw)
        return shape_template(template, record.handle, budget=max_response_chars())

    async def _run_template(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        kwargs = self.arguments(MCP_TOOL_RUN_TEMPLATE, arguments)
        inputs = kwargs.get("inputs")
        screen(MCP_TOOL_RUN_TEMPLATE, url_values(inputs) if isinstance(inputs, Mapping) else [])
        with template_charged(self.router, kwargs):
            response = await self.router.run_template(**kwargs)
        record = self.store.add(MCP_TOOL_RUN_TEMPLATE, raw=response.raw)
        return shape_template_run(response, record.handle, budget=max_response_chars())

    async def _answer(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        response = await self.router.answer(**self.arguments(MCP_TOOL_ANSWER, arguments))
        record = self.store.add(MCP_TOOL_ANSWER, raw=response.raw)
        return shape_answer(response, record.handle, budget=max_response_chars())

    async def _research(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        job = await self.router.submit_research(**self.arguments(MCP_TOOL_RESEARCH, arguments))
        try:
            response = await job.aresult(timeout=(waited := wait_left(self.research_wait)))
        except ResearchTimeoutError:
            # Not a failure: the run is still going, and a stdio session cannot
            # be held open for the ten minutes it may take.
            handle = self.store.add(MCP_TOOL_RESEARCH, job=job).handle
            return shape_pending(job, handle, wait=waited, note=MCP_MSG_RESEARCH_PENDING)
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
        kwargs = self.arguments(MCP_TOOL_BROWSE, arguments)
        screen(MCP_TOOL_BROWSE, [kwargs["start_url"]] if "start_url" in kwargs else [])
        job = await self.router.submit_browse(**kwargs)
        try:
            response = await job.aresult(timeout=(waited := wait_left(self.browse_wait)))
        except BrowseTimeoutError:
            # As for research: the task is still going, and is polled rather than waited on.
            handle = self.store.add(MCP_TOOL_BROWSE, job=job).handle
            return shape_pending(job, handle, wait=waited, note=MCP_MSG_BROWSE_PENDING)
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
        kwargs = self.arguments(MCP_TOOL_LOAD_ENV, arguments)
        path, remember = kwargs[MCP_ARG_PATH], kwargs.get(MCP_ARG_REMEMBER, False)
        if not isinstance(path, str) or not isinstance(remember, bool):
            raise ValueError(MCP_MSG_ENV_BAD_ARGUMENTS)
        applied = self.env.load(path, remember=remember)
        if applied.loaded:
            self._rebuild()
        payload: dict[str, Any] = {"file": str(applied.path), "loaded": list(applied.loaded)}
        if applied.already_set:
            payload["already_set"] = list(applied.already_set)
        payload["configured"] = methods.configured()
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
        kwargs = self.arguments(MCP_TOOL_BROWSER_OPEN, arguments)
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
        with browser_charged(self.router, kwargs):
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
        session_id = str(self.arguments(MCP_TOOL_BROWSER_CLOSE, arguments)[MCP_ARG_SESSION_ID])
        ended = await self.browsers.close(session_id, MCP_BROWSER_END_CLOSED)
        payload: dict[str, Any] = {MCP_ARG_SESSION_ID: session_id, "state": "released"}
        if ended is not None:
            payload["note"] = MCP_MSG_BROWSER_ALREADY_ENDED.format(reason=ended)
        return payload

    def _session_kwargs(self, tool: str, arguments: Mapping[str, Any]) -> tuple[str, dict[str, Any], list[str]]:
        """The session id, the action's keyword arguments, and a note for each one lowered to its limit."""
        kwargs = self.arguments(tool, arguments)
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
        handle = str(self.arguments(tool, arguments)[MCP_ARG_HANDLE])
        record = self.store.get(handle)
        if record is None:
            raise ValueError(MCP_MSG_UNKNOWN_HANDLE.format(handle=handle, limit=self.store.limit))
        return record

    def arguments(self, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """The library call's keyword arguments, taken straight from the tool's own.

        The tool's parameter names *are* the library's, so there is no mapping
        table to drift. :meth:`call` has already refused a name the tool does not
        declare; one left unset stays unset rather than being sent as a default of ours.
        """
        names = methods.argument_names(tool)
        # `is None`, not truthiness: browser_type's required `text` may be ""
        # -- the way a field is cleared -- and supplied-but-empty is not missing.
        missing = [name for name in MCP_TOOL_REQUIRED[tool] if arguments.get(name) is None]
        if MCP_ARG_PROVIDER in missing and not (method := METHODS[tool]).configured():
            # The schema offers no `provider` when there is none: say what would add one, not that it is missing.
            env_vars = ", ".join(methods.env_vars(method))
            raise ValueError(MCP_MSG_NOTHING_CONFIGURED.format(tool=tool, env_vars=env_vars))
        if missing:
            raise ValueError(MCP_MSG_MISSING_ARGUMENT.format(tool=tool, name=missing[0]))
        return {name: arguments[name] for name in names if arguments.get(name) is not None}

    def _description(self, tool: str) -> str:
        """The tool's description, plus a warning when nothing can serve it here."""
        description = MCP_TOOL_DESCRIPTIONS[tool]
        if tool in self._waits:
            description = description.format(wait=self._waits[tool])
        elif tool == MCP_TOOL_BROWSER_OPEN:
            description = description.format(idle=self.browsers.idle_timeout, max_sessions=self.browsers.max_sessions)
        elif tool == MCP_TOOL_BROWSER_SNAPSHOT:
            description = description.format(max_chars=MCP_BROWSER_SNAPSHOT_MAX_CHARS)
        method = METHODS.get(tool)
        if method is not None and not methods.usable(method):
            description += MCP_MSG_NO_BACKEND
            if names := methods.env_vars(method):
                description += MCP_MSG_NO_BACKEND_HINT.format(env_vars=", ".join(names))
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
        method = METHODS.get(tool)
        providers = method.configured() if method is not None else []
        if method is not None:
            serving = methods.serving(tool, [*providers, self.router.default_provider], self.router)
            mapped = frozenset().union(*(cls.supported_parameters for cls in serving))
            properties = {name: spec for name, spec in properties.items() if name not in method.options - mapped}
            if MCP_ARG_STEALTH in properties and tool == MCP_TOOL_BROWSER_OPEN:
                methods.offer_levels(properties, serving)
            if MCP_ARG_FORMAT in properties and tool == MCP_TOOL_RUN_TEMPLATE:
                methods.offer_formats(properties, serving)
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
