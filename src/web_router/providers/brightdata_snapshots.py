"""Waiting on a Bright Data snapshot: a scrape that outlasted the synchronous window, read once it is ready.

Bright Data answers a scrape that runs past about a minute with 202 and a
``snapshot_id``; the scrape goes on and is billed. :class:`SnapshotWait` is
:class:`~web_router.providers._job_wait.JobWait` with Bright Data's paths,
statuses and messages, so every error after that point is a
:class:`~web_router.exceptions_live_data.LiveDataJobError` naming the snapshot
and the records can be read later rather than paid for again.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, ClassVar, Final

from web_router.constants_live_data_vendors import (
    BRIGHTDATA_PROGRESS_PATH,
    BRIGHTDATA_SNAPSHOT_CANCELED,
    BRIGHTDATA_SNAPSHOT_FAILED,
    BRIGHTDATA_SNAPSHOT_PATH,
    BRIGHTDATA_SNAPSHOT_READY,
    MSG_BRIGHTDATA_NO_REASON,
    MSG_BRIGHTDATA_NO_SNAPSHOT,
    MSG_BRIGHTDATA_PENDING,
    MSG_BRIGHTDATA_POLL_FAILED,
    MSG_BRIGHTDATA_SNAPSHOT_CANCELED,
    MSG_BRIGHTDATA_SNAPSHOT_FAILED,
)
from web_router.exceptions import ProviderError
from web_router.exceptions_live_data import LiveDataJobError
from web_router.providers._deadline import job_deadline
from web_router.providers._job_wait import JobWait, wait_until
from web_router.providers.brightdata_shapes import to_response
from web_router.types_live_data import LiveDataRequest, LiveDataResponse

if TYPE_CHECKING:
    from web_router.providers.brightdata_live_data import BrightDataLiveDataProvider

SNAPSHOT_READ: Final = {"format": "json"}
"""A snapshot's records as JSON. The snapshot endpoint refuses ``include_errors`` (HTTP 400, checked live on
2026-11-12); a failed input's row comes back with the records either way, and lands in ``extra["errors"]``."""


def snapshot_id(provider: str, request: LiveDataRequest, payload: Any) -> str:
    """The id of the snapshot a 202 named; raises when there is none, as there is then nothing to wait on."""
    found = str((payload if isinstance(payload, dict) else {}).get("snapshot_id") or "")
    if not found:
        raise ProviderError(provider, MSG_BRIGHTDATA_NO_SNAPSHOT.format(source=request.source))
    return found


class SnapshotWait(JobWait):
    """One snapshot being waited on: its progress polled until it is ready, then its records read."""

    still_running: ClassVar[str] = MSG_BRIGHTDATA_PENDING

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


def wait_and_read(provider: BrightDataLiveDataProvider, request: LiveDataRequest, response: Any) -> LiveDataResponse:
    """Wait for the snapshot a scrape's 202 ``response`` names, then return its records.

    The wait lasts up to the provider's ``wait_seconds``, or until just before
    the call's deadline if sooner, polling every ``poll_seconds``. Every
    failure past the 202 names the snapshot: a poll or the read that fails, a
    snapshot that failed or was canceled, and a wait that ran out. A ready
    snapshot whose inputs all failed raises as a synchronous run would. The wait
    is checked before each poll, so a 202 that leaves no time raises with the
    snapshot's id rather than sending a poll the caller's deadline cuts off.
    """
    snapshot = _waiting(provider, request, response)
    with job_deadline(snapshot.until):
        while True:
            snapshot.check()
            with snapshot.naming(MSG_BRIGHTDATA_POLL_FAILED):
                if snapshot.done(provider._send("GET", snapshot.path)):
                    break
            time.sleep(snapshot.pause())
        with snapshot.naming(MSG_BRIGHTDATA_POLL_FAILED):
            read = provider._client.get(snapshot.records_path, params=SNAPSHOT_READ, **provider._clamped())
            payload = provider._decoded(read)
        return to_response(provider.name, request, read.status_code, payload)


async def await_and_read(
    provider: BrightDataLiveDataProvider, request: LiveDataRequest, response: Any
) -> LiveDataResponse:
    """Async counterpart of :func:`wait_and_read`."""
    snapshot = _waiting(provider, request, response)
    with job_deadline(snapshot.until):
        while True:
            snapshot.check()
            with snapshot.naming(MSG_BRIGHTDATA_POLL_FAILED):
                if snapshot.done(await provider._asend("GET", snapshot.path)):
                    break
            await asyncio.sleep(snapshot.pause())
        with snapshot.naming(MSG_BRIGHTDATA_POLL_FAILED):
            read = await provider._async_client.get(snapshot.records_path, params=SNAPSHOT_READ, **provider._clamped())
            payload = provider._decoded(read)
        return to_response(provider.name, request, read.status_code, payload)


def _waiting(provider: BrightDataLiveDataProvider, request: LiveDataRequest, response: Any) -> SnapshotWait:
    found = snapshot_id(provider.name, request, provider._decoded(response))
    return SnapshotWait(provider.name, request, found, wait_until(provider.wait_seconds), provider.poll_seconds)
