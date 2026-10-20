"""Browserbase providers: browser sessions over the Sessions API, and browse tasks over the Agents API.

Talks to the REST endpoints directly rather than through the ``browserbase``
SDK: each method is a handful of requests, which an SDK dependency would not
simplify. Install with the ``all`` extra. Both read
``BROWSERBASE_API_KEY`` unless a key is passed explicitly. No project id is
needed: Browserbase infers the project from the key.

The session provider now also maps ``stealth``, ``solve_captchas`` and
``proxy_country`` -- all three **unverified**: taken from
https://docs.browserbase.com/reference/api/create-a-session and its stealth
(https://docs.browserbase.com/features/stealth-mode,
https://docs.browserbase.com/platform/identity/overview) and proxies
(https://docs.browserbase.com/platform/identity/proxies) guides, read on
2026-09-24, not from a live call -- no account was available. The live test in
``tests/providers/test_browserbase_browser.py`` is the check that confirms them.

- ``stealth``: the reference deprecates ``browserSettings.advancedStealth`` in
  favor of ``browserSettings.verified``, a same-shaped boolean gated on the
  Scale plan, so ``verified`` is what ``"low"`` (``false``, the ordinary
  session) and ``"high"`` (``true``) set. Neither the reference nor the guides
  show a field for turning a session's baseline evasion off, so ``"off"`` is
  not in ``stealth_levels`` and is refused rather than served as ``"low"``.
- ``solve_captchas`` -> ``browserSettings.solveCaptchas``, on by default;
  sending ``false`` turns it off.
- ``proxy_country`` -> ``proxies: [{"type": "browserbase", "geolocation":
  {"country": ...}}]``, which both names the country and turns Browserbase's
  proxy on -- there is no separate switch. The guide calls geolocation fields
  case-insensitive; the country is still sent upper-case, matching the
  reference's own examples. No published list of every supported country was
  found short of an illustrative, non-exhaustive accordion, so
  ``proxy_countries`` is left ``None`` and Browserbase is left to refuse a code
  it has no proxy in.

``profile`` and ``persist_profile`` map onto a context,
``browserSettings.context.id`` and ``.persist``, and the session's live view
is fetched from ``GET /v1/sessions/{id}/debug`` when the handle's
``control_url()`` asks -- all three checked live on 2026-10-20. Still
reachable only through ``provider_options``: viewport and keep-alive;
``idle_timeout`` is refused, as Browserbase documents no idle limit.
The browse provider maps only the task: the Agents API has no start URL, and
its ``resultSchema``, ``agentId``, ``variables`` and ``browserSettings`` go
through ``provider_options`` until a live call shows what they return. Mapping
one properly is a welcome PR -- see CONTRIBUTING.md.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Any, ClassVar

import httpx
from pydantic import SecretStr, TypeAdapter, ValidationError

from web_router.constants import (
    BROWSER_PERSIST_PROFILE_REQUIRES,
    BROWSERBASE_API_KEY_HEADER,
    BROWSERBASE_BASE_URL,
    BROWSERBASE_BROWSE_RUN_PATH,
    BROWSERBASE_BROWSE_START_PATH,
    BROWSERBASE_BROWSE_STATES,
    BROWSERBASE_BROWSE_STOP_PATH,
    BROWSERBASE_CONTROL_URL_FIELD,
    BROWSERBASE_DEBUG_PATH,
    BROWSERBASE_FIELDS,
    BROWSERBASE_OPEN_PATH,
    BROWSERBASE_RELEASE_BODY,
    BROWSERBASE_RESULT_SCHEMA_FIELD,
    BROWSERBASE_RESULT_SUMMARY_FIELD,
    BROWSERBASE_SESSION_PATH,
    BROWSERBASE_SESSION_STATES,
    BROWSERBASE_STEALTH,
    ENV_BROWSERBASE_API_KEY,
    PROVIDER_BROWSERBASE,
)
from web_router.exceptions import ProviderError
from web_router.providers._http import HttpBrowseProvider, HttpBrowserProvider, ReleaseMethod
from web_router.providers._utils import build_browser_payload, build_payload
from web_router.types import (
    BrowseProgress,
    BrowseRequest,
    BrowseResponse,
    BrowserRequest,
    BrowserSession,
    BrowserState,
    JobState,
    StealthLevel,
)

_TIMESTAMP: TypeAdapter[datetime] = TypeAdapter(datetime)


def _seconds(seconds: float) -> int:
    # Browserbase's `timeout` is an integer, and BROWSER_TIMEOUT is a float.
    # Rounded up, so a session never ends sooner than the caller asked.
    return math.ceil(seconds)


def _timestamp(value: Any) -> datetime | None:
    # An expiry is a courtesy; one that cannot be read must not cost the caller
    # a session that opened and bills, so it is left unset instead.
    if not isinstance(value, str):
        return None
    try:
        return _TIMESTAMP.validate_python(value)
    except ValidationError:
        return None


class BrowserbaseBrowserProvider(HttpBrowserProvider):
    """Opens cloud browser sessions through the Browserbase Sessions API."""

    name: ClassVar[str] = PROVIDER_BROWSERBASE
    supported_parameters: ClassVar[frozenset[str]] = frozenset(
        {"timeout", "stealth", "solve_captchas", "proxy_country", "profile", "persist_profile"}
    )
    stealth_levels: ClassVar[frozenset[StealthLevel]] = frozenset(BROWSERBASE_STEALTH)
    proxy_countries: ClassVar[frozenset[str] | None] = None
    co_requirements: ClassVar[Mapping[str, Mapping[Any, frozenset[str]]]] = MappingProxyType(
        BROWSER_PERSIST_PROFILE_REQUIRES
    )

    base_url: ClassVar[str] = BROWSERBASE_BASE_URL
    open_path: ClassVar[str] = BROWSERBASE_OPEN_PATH
    release_path: ClassVar[str] = BROWSERBASE_SESSION_PATH
    release_method: ClassVar[ReleaseMethod] = "POST"
    # Browserbase has no release call: a session is ended by updating it with a
    # status that asks for one, so the body is what does the work.
    release_body: ClassVar[dict[str, Any] | None] = BROWSERBASE_RELEASE_BODY
    env_api_key: ClassVar[str] = ENV_BROWSERBASE_API_KEY
    api_key_header: ClassVar[str] = BROWSERBASE_API_KEY_HEADER

    def _build_payload(self, request: BrowserRequest) -> dict[str, Any]:
        return build_browser_payload(request, BROWSERBASE_FIELDS, _seconds)

    def _to_session(self, request: BrowserRequest, payload: dict[str, Any]) -> BrowserSession:
        # The live view is a second call, made only when the handle's
        # `control_url()` asks for it, not on every open for a URL most callers
        # never look at. It is interactive, so it is never `live_view_url`.
        # `signingKey` stays in `raw` alone.
        return BrowserSession(
            provider=self.name,
            session_id=payload["id"],
            cdp_url=SecretStr(payload["connectUrl"]),
            expires_at=_timestamp(payload.get("expiresAt")),
            raw=payload,
        )

    def _fetch(self, session_id: str) -> BrowserState:
        return self._state(self._get(self._session_path(session_id)))

    async def _afetch(self, session_id: str) -> BrowserState:
        return self._state(await self._aget(self._session_path(session_id)))

    def _control_url(self, session_id: str) -> str | None:
        return self._get(self._session_path(session_id, BROWSERBASE_DEBUG_PATH)).get(BROWSERBASE_CONTROL_URL_FIELD)

    async def _acontrol_url(self, session_id: str) -> str | None:
        payload = await self._aget(self._session_path(session_id, BROWSERBASE_DEBUG_PATH))
        return payload.get(BROWSERBASE_CONTROL_URL_FIELD)

    def _state(self, payload: dict[str, Any]) -> BrowserState:
        return self.state_of(payload["status"], BROWSERBASE_SESSION_STATES)


class BrowserbaseBrowseProvider(HttpBrowseProvider):
    """Runs hosted browser-agent tasks through the Browserbase Agents API.

    A run is started with the task alone, polled until Browserbase reports it
    finished, and stopped on request. The run's ``sessionId`` names a live,
    billed browser while the run lasts, so it stays in ``raw``.

    A run with no ``agentId`` may create a new Agent in the account each time:
    the "How it works" page says it does, the Run an Agent reference calls the
    agent optional and says nothing of creating one. Pass an existing
    ``agentId`` through ``provider_options`` to reuse one.
    """

    name: ClassVar[str] = PROVIDER_BROWSERBASE
    # `start_url` has no field in the Agents API, so it is refused rather than
    # folded into the task. `resultSchema` reaches it through provider_options;
    # mapping `output_schema` onto it waits for a live call.
    supported_parameters: ClassVar[frozenset[str]] = frozenset()

    base_url: ClassVar[str] = BROWSERBASE_BASE_URL
    env_api_key: ClassVar[str] = ENV_BROWSERBASE_API_KEY
    api_key_header: ClassVar[str] = BROWSERBASE_API_KEY_HEADER

    def _start(self, request: BrowseRequest) -> BrowseProgress:
        return self._progress(request, self._post(BROWSERBASE_BROWSE_START_PATH, self._build_payload(request)))

    async def _astart(self, request: BrowseRequest) -> BrowseProgress:
        return self._progress(request, await self._apost(BROWSERBASE_BROWSE_START_PATH, self._build_payload(request)))

    def _fetch(self, request: BrowseRequest, job_id: str) -> BrowseProgress:
        return self._progress(request, self._get(self._task_path(BROWSERBASE_BROWSE_RUN_PATH, job_id)))

    async def _afetch(self, request: BrowseRequest, job_id: str) -> BrowseProgress:
        return self._progress(request, await self._aget(self._task_path(BROWSERBASE_BROWSE_RUN_PATH, job_id)))

    def _cancel(self, job_id: str) -> bool:
        try:
            self._call("POST", self._task_path(BROWSERBASE_BROWSE_STOP_PATH, job_id))
        except ProviderError as exc:
            if _already_finished(exc):
                return False
            raise
        return True

    async def _acancel(self, job_id: str) -> bool:
        try:
            await self._acall("POST", self._task_path(BROWSERBASE_BROWSE_STOP_PATH, job_id))
        except ProviderError as exc:
            if _already_finished(exc):
                return False
            raise
        return True

    def _build_payload(self, request: BrowseRequest) -> dict[str, Any]:
        return build_payload(request, task=request.task)

    def _progress(self, request: BrowseRequest, payload: dict[str, Any]) -> BrowseProgress:
        state = self.state_of(str(payload.get("status")), BROWSERBASE_BROWSE_STATES)
        cause = payload.get("cause") or {}
        return BrowseProgress(
            job_id=payload["runId"],
            state=state,
            # The run carries its result, so a finished run needs no second call.
            response=self._to_response(request, payload) if state == "completed" else None,
            # A TIMED_OUT run may carry no cause, and must still say why it ended.
            detail=cause.get("message") or cause.get("code") or (None if state in _ACTIVE else payload.get("status")),
        )

    def _to_response(self, request: BrowseRequest, payload: dict[str, Any]) -> BrowseResponse:
        result = payload.get("result")
        # Only a run given a resultSchema returns structured output; without one
        # the answer is prose, which a live run returned inside an object as its
        # summary. The step transcript is a separate, paginated messages endpoint
        # whose shape the docs describe two ways, so `steps` stays empty.
        if BROWSERBASE_RESULT_SCHEMA_FIELD in request.provider_options:
            answer, structured = None, result if isinstance(result, (dict, list)) else None
        elif isinstance(result, dict):
            summary = result.get(BROWSERBASE_RESULT_SUMMARY_FIELD)
            answer, structured = (summary if isinstance(summary, str) else None), None
        else:
            answer, structured = (result if isinstance(result, str) else None), None
        return BrowseResponse(
            provider=self.name,
            task=request.task,
            answer=answer,
            structured=structured,
            request_id=payload["runId"],
            raw=[payload],
        )


_ACTIVE: frozenset[JobState] = frozenset({"queued", "running"})


def _already_finished(exc: ProviderError) -> bool:
    # Browserbase answers a stop on a finished run with 409, and a job that has
    # already stopped is one the caller cannot cancel -- not an error.
    original = exc.original
    return isinstance(original, httpx.HTTPStatusError) and original.response.status_code == httpx.codes.CONFLICT
