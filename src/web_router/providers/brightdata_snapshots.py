"""Waiting on a Bright Data snapshot: a scrape that outlasted the synchronous window, read once it is ready.

Bright Data answers a scrape that runs past about a minute with 202 and a
``snapshot_id``; the scrape goes on and is billed. :class:`SnapshotWait` is
Oxylabs' :class:`~web_router.providers.oxylabs_jobs.JobWait` with Bright
Data's paths, statuses and messages, so every error after that point is a
:class:`~web_router.exceptions_live_data.LiveDataJobError` naming the snapshot
and the records can be read later rather than paid for again.
"""

from __future__ import annotations

import time
from typing import Any

from web_router.constants_live_data_vendors import (
    BRIGHTDATA_PROGRESS_PATH,
    BRIGHTDATA_SNAPSHOT_CANCELED,
    BRIGHTDATA_SNAPSHOT_FAILED,
    BRIGHTDATA_SNAPSHOT_PATH,
    BRIGHTDATA_SNAPSHOT_READY,
    MSG_BRIGHTDATA_NO_REASON,
    MSG_BRIGHTDATA_NO_SNAPSHOT,
    MSG_BRIGHTDATA_PENDING,
    MSG_BRIGHTDATA_SNAPSHOT_CANCELED,
    MSG_BRIGHTDATA_SNAPSHOT_FAILED,
)
from web_router.exceptions import ProviderError
from web_router.exceptions_live_data import LiveDataJobError
from web_router.providers.oxylabs_jobs import JobWait
from web_router.types_live_data import LiveDataRequest


def snapshot_id(provider: str, request: LiveDataRequest, payload: Any) -> str:
    """The id of the snapshot a 202 named; raises when there is none, as there is then nothing to wait on."""
    found = str((payload if isinstance(payload, dict) else {}).get("snapshot_id") or "")
    if not found:
        raise ProviderError(provider, MSG_BRIGHTDATA_NO_SNAPSHOT.format(source=request.source))
    return found


class SnapshotWait(JobWait):
    """One snapshot being waited on: its progress polled until it is ready, then its records read."""

    @property
    def path(self) -> str:
        return f"{BRIGHTDATA_PROGRESS_PATH}/{self.job_id}"

    @property
    def records_path(self) -> str:
        return f"{BRIGHTDATA_SNAPSHOT_PATH}/{self.job_id}"

    def done(self, payload: Any) -> bool:
        """Whether the snapshot is ready to read; raises when it failed or was canceled, as it then never will be."""
        body = payload if isinstance(payload, dict) else {}
        status = body.get("status")
        terminal = {
            BRIGHTDATA_SNAPSHOT_FAILED: MSG_BRIGHTDATA_SNAPSHOT_FAILED,
            BRIGHTDATA_SNAPSHOT_CANCELED: MSG_BRIGHTDATA_SNAPSHOT_CANCELED,
        }
        if status in terminal:
            reason = body.get("error_message") or MSG_BRIGHTDATA_NO_REASON
            message = terminal[status].format(job_id=self.job_id, source=self.request.source, reason=reason)
            raise LiveDataJobError(self.provider, message, job_id=self.job_id)
        return status == BRIGHTDATA_SNAPSHOT_READY

    def check(self) -> None:
        """Raise that the scrape is still running once :attr:`until` has passed."""
        if time.monotonic() >= self.until:
            seconds = self.until - self.started
            message = MSG_BRIGHTDATA_PENDING.format(source=self.request.source, seconds=seconds, job_id=self.job_id)
            raise LiveDataJobError(self.provider, message, job_id=self.job_id)
