"""Browser Use provider, over the Cloud API v2 Tasks endpoints.

Browse only. Talks to the REST endpoint directly rather than through the
``browser-use-sdk``: web-router starts, polls and stops a task, which an SDK
dependency would not simplify. Install with the ``all`` extra.
Reads ``BROWSER_USE_API_KEY`` unless a key is passed explicitly.

v2 rather than v4, because v2 takes a start URL, a schema, a step limit and a
domain list as fields of its own; each is mapped, and each was checked against
a live task on 2026-10-21. The model and the session settings are reachable
through ``provider_options``.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from web_router.constants import (
    BROWSER_USE_API_KEY_HEADER,
    BROWSER_USE_BASE_URL,
    BROWSER_USE_FIELDS,
    BROWSER_USE_SCHEMA_FIELD,
    BROWSER_USE_SESSION_ID_OPTION,
    BROWSER_USE_STEP_FIELDS,
    BROWSER_USE_STOP_BODY,
    BROWSER_USE_STOP_WITH_SESSION_BODY,
    BROWSER_USE_TASK_PATH,
    BROWSER_USE_TASK_STATES,
    BROWSER_USE_TASK_STATUS_PATH,
    BROWSER_USE_TASKS_PATH,
    ENV_BROWSER_USE_API_KEY,
    PROVIDER_BROWSER_USE,
)
from web_router.providers._http import HttpBrowseProvider
from web_router.providers._utils import build_payload
from web_router.types import BrowseProgress, BrowseRequest, BrowseResponse, BrowseStep, JobState


class BrowserUseBrowseProvider(HttpBrowseProvider):
    """Runs browse tasks on Browser Use Cloud's agent, through the v2 Tasks API.

    A task is polled on the lightweight status call, as Browser Use asks, and
    read once in full when it has finished, for its steps -- which is why a
    finished task keeps two payloads in :attr:`~web_router.BrowseResponse.raw`.
    """

    name: ClassVar[str] = PROVIDER_BROWSER_USE
    supported_parameters: ClassVar[frozenset[str]] = frozenset(
        {"start_url", "output_schema", "max_steps", "allowed_domains"}
    )

    base_url: ClassVar[str] = BROWSER_USE_BASE_URL
    env_api_key: ClassVar[str] = ENV_BROWSER_USE_API_KEY
    api_key_header: ClassVar[str] = BROWSER_USE_API_KEY_HEADER

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Tasks this provider started in a session Browser Use created for
        # them, whose session a cancel may stop too. Any other id -- one run
        # in the caller's own session, or one started elsewhere -- is stopped
        # alone, because its session may not be ours to end.
        self._own_sessions: set[str] = set()

    def _start(self, request: BrowseRequest) -> BrowseProgress:
        return self._started(request, self._post(BROWSER_USE_TASKS_PATH, self._build_payload(request)))

    async def _astart(self, request: BrowseRequest) -> BrowseProgress:
        return self._started(request, await self._apost(BROWSER_USE_TASKS_PATH, self._build_payload(request)))

    def _fetch(self, request: BrowseRequest, job_id: str) -> BrowseProgress:
        status = self._get(self._task_path(BROWSER_USE_TASK_STATUS_PATH, job_id))
        state = self._state(status)
        if state != "completed":
            return self._progress(job_id, state, status)
        task = self._get(self._task_path(BROWSER_USE_TASK_PATH, job_id))
        return self._progress(job_id, state, status, self._to_response(request, status, task))

    async def _afetch(self, request: BrowseRequest, job_id: str) -> BrowseProgress:
        status = await self._aget(self._task_path(BROWSER_USE_TASK_STATUS_PATH, job_id))
        state = self._state(status)
        if state != "completed":
            return self._progress(job_id, state, status)
        task = await self._aget(self._task_path(BROWSER_USE_TASK_PATH, job_id))
        return self._progress(job_id, state, status, self._to_response(request, status, task))

    def _cancel(self, job_id: str) -> bool:
        self._call("PATCH", self._task_path(BROWSER_USE_TASK_PATH, job_id), self._stop_body(job_id))
        return True

    async def _acancel(self, job_id: str) -> bool:
        await self._acall("PATCH", self._task_path(BROWSER_USE_TASK_PATH, job_id), self._stop_body(job_id))
        return True

    def _stop_body(self, job_id: str) -> dict[str, str]:
        return BROWSER_USE_STOP_WITH_SESSION_BODY if job_id in self._own_sessions else BROWSER_USE_STOP_BODY

    def _build_payload(self, request: BrowseRequest) -> dict[str, Any]:
        schema = request.output_schema
        return build_payload(
            request,
            BROWSER_USE_FIELDS,
            task=request.task,
            **({BROWSER_USE_SCHEMA_FIELD: json.dumps(schema)} if schema is not None else {}),
        )

    def _started(self, request: BrowseRequest, created: dict[str, Any]) -> BrowseProgress:
        # The create call answers 202 with the task and session ids and no
        # status at all, so the task is reported as accepted and not yet
        # running -- what `created` means -- until the first poll says more.
        job_id = str(created["id"])
        if request.provider_options.get(BROWSER_USE_SESSION_ID_OPTION) is None:
            self._own_sessions.add(job_id)
        return BrowseProgress(job_id=job_id, state="queued")

    def _state(self, status: dict[str, Any]) -> JobState:
        return self.state_of(str(status.get("status")), BROWSER_USE_TASK_STATES)

    def _progress(
        self, job_id: str, state: JobState, status: dict[str, Any], response: BrowseResponse | None = None
    ) -> BrowseProgress:
        # There is no error field: a task that failed or was stopped says why,
        # when it says anything, in the same `output` a finished one answers in.
        output = status.get("output")
        detail = output if state != "completed" and isinstance(output, str) and output else None
        return BrowseProgress(job_id=job_id, state=state, response=response, detail=detail)

    def _to_response(self, request: BrowseRequest, status: dict[str, Any], task: dict[str, Any]) -> BrowseResponse:
        # `output` is always a string: the schema, when one is sent, travels
        # stringified and its answer comes back the same way. The session's live
        # URL is not in either payload, and nothing but `raw` would carry it if
        # it were.
        output = status.get("output")
        answer = output if isinstance(output, str) and output else None
        structured = _structured(answer) if request.output_schema is not None else None
        return BrowseResponse(
            provider=self.name,
            task=request.task,
            answer=None if structured is not None else answer,
            structured=structured,
            steps=[self._step(step) for step in task.get("steps") or []],
            request_id=str(status.get("id") or task.get("id") or "") or None,
            raw=[status, task],
        )

    @staticmethod
    def _step(step: dict[str, Any]) -> BrowseStep:
        # `actions` is a list of stringified actions and `action` one string, so
        # only a step that took a single action has one; joining several would
        # be a format Browser Use never wrote. The list stays whole in `extra`.
        actions = step.get("actions") or []
        return BrowseStep(
            action=actions[0] if len(actions) == 1 and isinstance(actions[0], str) else None,
            url=step.get("url") or None,
            detail=step.get("memory") or None,
            screenshot_url=step.get("screenshotUrl") or None,
            extra={key: value for key, value in step.items() if key not in BROWSER_USE_STEP_FIELDS},
        )


def _structured(output: str | None) -> dict[str, Any] | list[Any] | None:
    # An answer that is not JSON is left as prose rather than dropped: the agent
    # was asked for the schema, but only its output says whether it managed.
    # A bare JSON value (a `{"type": "string"}` schema's `"Paris"`) does not fit
    # `structured` either and stays in `answer` as the JSON text Browser Use
    # returned, quotes included.
    if output is None:
        return None
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, (dict, list)) else None
