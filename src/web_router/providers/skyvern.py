"""Skyvern provider, over the run-task API.

Browse only. Talks to the REST endpoints directly rather than through the
``skyvern`` SDK: web-router starts, reads and cancels a run, which an SDK
dependency would not simplify. Install with the ``all`` extra.
Reads ``SKYVERN_API_KEY`` unless a key is passed explicitly.

Slim by design: only ``start_url`` is mapped, and it has not been confirmed
against a live call. Skyvern's ``data_extraction_schema``, ``max_steps``,
engine, proxy location and browser session are all reachable through
``provider_options``; mapping one properly is a welcome PR -- see
CONTRIBUTING.md.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import (
    ENV_SKYVERN_API_KEY,
    PROVIDER_SKYVERN,
    SKYVERN_API_KEY_HEADER,
    SKYVERN_BASE_URL,
    SKYVERN_BROWSE_FIELDS,
    SKYVERN_CANCEL_PATH,
    SKYVERN_REPORTED_STATUSES,
    SKYVERN_RUN_PATH,
    SKYVERN_RUN_TASK_PATH,
    SKYVERN_TASK_STATES,
)
from web_router.providers._http import HttpBrowseProvider
from web_router.providers._utils import build_payload
from web_router.types import BrowseProgress, BrowseRequest, BrowseResponse


class SkyvernBrowseProvider(HttpBrowseProvider):
    """Runs browse tasks through Skyvern Cloud's run-task API.

    A task is started with one call and read back with another, whose answer
    carries the ``output`` once the run has completed. ``start_url`` is
    optional: sent no ``url``, Skyvern tries to determine one itself. The start
    call, ``POST /v1/run/tasks``, is marked ``x-hidden`` in Skyvern's OpenAPI
    spec but is the REST path its guides and SDK reference document.

    Until ``data_extraction_schema`` is mapped, most answers are only in
    :attr:`~web_router.BrowseResponse.raw`: Skyvern answers in an object whose
    keys its agent chose, which is not a schema's answer, so only a string
    output becomes ``answer`` and ``structured`` stays empty. ``steps`` stays
    empty too, because the run reports only a ``step_count``. The
    ``recording_url`` (a presigned link carrying credentials) and the
    ``app_url`` dashboard link stay in ``raw`` as well.
    """

    name: ClassVar[str] = PROVIDER_SKYVERN
    supported_parameters: ClassVar[frozenset[str]] = frozenset(SKYVERN_BROWSE_FIELDS)

    base_url: ClassVar[str] = SKYVERN_BASE_URL
    env_api_key: ClassVar[str] = ENV_SKYVERN_API_KEY
    api_key_header: ClassVar[str] = SKYVERN_API_KEY_HEADER

    def _start(self, request: BrowseRequest) -> BrowseProgress:
        return self._progress(request, self._post(SKYVERN_RUN_TASK_PATH, self._build_payload(request)))

    async def _astart(self, request: BrowseRequest) -> BrowseProgress:
        return self._progress(request, await self._apost(SKYVERN_RUN_TASK_PATH, self._build_payload(request)))

    def _fetch(self, request: BrowseRequest, job_id: str) -> BrowseProgress:
        return self._progress(request, self._get(self._task_path(SKYVERN_RUN_PATH, job_id)))

    async def _afetch(self, request: BrowseRequest, job_id: str) -> BrowseProgress:
        return self._progress(request, await self._aget(self._task_path(SKYVERN_RUN_PATH, job_id)))

    def _cancel(self, job_id: str) -> bool:
        # Answered with an untyped body, so success is the status alone. A run
        # that already finished is a 200 no-op too, so True means Skyvern
        # accepted the request, not that a running task stopped; the job reads
        # the run's own status afterwards for that.
        self._call("POST", self._task_path(SKYVERN_CANCEL_PATH, job_id))
        return True

    async def _acancel(self, job_id: str) -> bool:
        await self._acall("POST", self._task_path(SKYVERN_CANCEL_PATH, job_id))
        return True

    def _build_payload(self, request: BrowseRequest) -> dict[str, Any]:
        return build_payload(request, SKYVERN_BROWSE_FIELDS, prompt=request.task)

    def _progress(self, request: BrowseRequest, run: dict[str, Any]) -> BrowseProgress:
        state = self.state_of(str(run.get("status")), SKYVERN_TASK_STATES)
        detail = run.get("failure_reason")
        if detail is None and run.get("status") in SKYVERN_REPORTED_STATUSES:
            # `paused` reads as running, so its name is the only way a caller can tell the two apart.
            detail = run.get("status")
        return BrowseProgress(
            # A missing id surfaces here, at the malformed payload, rather than
            # as a poll against an empty id later -- as browser_use reads its own.
            job_id=str(run["run_id"]),
            state=state,
            response=self._to_response(request, run) if state == "completed" else None,
            detail=None if detail is None else str(detail),
        )

    def _to_response(self, request: BrowseRequest, run: dict[str, Any]) -> BrowseResponse:
        output = run.get("output")
        return BrowseResponse(
            provider=self.name,
            task=request.task,
            answer=output if isinstance(output, str) else None,
            request_id=str(run.get("run_id") or "") or None,
            raw=[run],
        )
