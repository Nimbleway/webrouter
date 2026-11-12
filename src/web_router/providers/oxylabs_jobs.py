"""An Oxylabs Web API job: its id, where it is read, and when it is done.

A scraper Oxylabs runs only as a job is submitted, then polled. Once it is
submitted it is billed, so every error after that point names the job;
:class:`~web_router.providers._job_wait.JobWait` holds the wait itself, shared
with Bright Data's snapshots.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants_live_data_vendors import (
    MSG_OXYLABS_FAULTED,
    MSG_OXYLABS_NO_JOB_ID,
    MSG_OXYLABS_STILL_RUNNING,
    OXYLABS_ASYNC_PREFIX,
    OXYLABS_JOB_DONE,
    OXYLABS_JOB_FAULTED,
)
from web_router.exceptions import ProviderError
from web_router.exceptions_live_data import LiveDataJobError
from web_router.providers._job_wait import JobWait
from web_router.types_live_data import LiveDataRequest


def job_id(provider: str, request: LiveDataRequest, payload: Any) -> str:
    """The id Oxylabs gave a submitted job; raises when the answer has none, as there is then nothing to poll."""
    found = str(((payload if isinstance(payload, dict) else {}).get("metadata") or {}).get("request_id") or "")
    if not found:
        raise ProviderError(provider, MSG_OXYLABS_NO_JOB_ID.format(source=request.source))
    return found


class OxylabsJob(JobWait):
    """One submitted Oxylabs job being waited on."""

    still_running: ClassVar[str] = MSG_OXYLABS_STILL_RUNNING

    @property
    def path(self) -> str:
        return f"{OXYLABS_ASYNC_PREFIX}/{self.job_id}"

    def done(self, payload: Any) -> bool:
        """Whether the job is done; raises when it faulted."""
        body = payload if isinstance(payload, dict) else {}
        state = body.get("status") or body.get("state")
        if state == OXYLABS_JOB_FAULTED:
            first = (body.get("results") or [{}])[0]
            status = (first.get("metadata") or {}).get("status_code")
            message = MSG_OXYLABS_FAULTED.format(job_id=self.job_id, source=self.request.source, status=status)
            raise LiveDataJobError(self.provider, message, job_id=self.job_id)
        return state == OXYLABS_JOB_DONE
