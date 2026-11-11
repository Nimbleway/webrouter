"""Waiting on an Oxylabs Web API job: when it is done, when to give up, and the errors that name it.

A scraper Oxylabs runs only as a job is submitted, then polled. Once it is
submitted it is billed, so every error after that point is a
:class:`~web_router.exceptions_live_data.LiveDataJobError` naming the job. The
provider owns the HTTP and the sleeping, sync or async; :class:`JobWait` holds
the rest, so the two loops are the same few lines.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, NoReturn

from web_router.constants_live_data_vendors import (
    MSG_OXYLABS_FAULTED,
    MSG_OXYLABS_NO_JOB_ID,
    MSG_OXYLABS_STILL_RUNNING,
    OXYLABS_ASYNC_PREFIX,
    OXYLABS_JOB_DEADLINE_MARGIN,
    OXYLABS_JOB_DONE,
    OXYLABS_JOB_FAULTED,
)
from web_router.exceptions import ProviderError, WebRouterError
from web_router.exceptions_live_data import LiveDataJobError
from web_router.providers._deadline import current_deadline
from web_router.types_live_data import LiveDataRequest


def job_id(provider: str, request: LiveDataRequest, payload: Any) -> str:
    """The id Oxylabs gave a submitted job; raises when the answer has none, as there is then nothing to poll."""
    found = str(((payload if isinstance(payload, dict) else {}).get("metadata") or {}).get("request_id") or "")
    if not found:
        raise ProviderError(provider, MSG_OXYLABS_NO_JOB_ID.format(source=request.source))
    return found


def wait_until(wait_seconds: float) -> float:
    """When a wait gives up: after ``wait_seconds``, or just before the call's deadline when that is sooner.

    Stopping short of the call's deadline is what keeps the job's id: at the
    deadline itself the router raises its own error, which names no job.
    """
    until = time.monotonic() + wait_seconds
    call = current_deadline()
    return until if call is None else min(until, call.at - OXYLABS_JOB_DEADLINE_MARGIN)


@dataclass
class JobWait:
    """One submitted job being waited on, up to :attr:`until`, polled every :attr:`poll_seconds`."""

    provider: str
    request: LiveDataRequest
    job_id: str
    until: float
    poll_seconds: float
    started: float = field(default_factory=time.monotonic)

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

    def pause(self) -> float:
        """Seconds to sleep before the next poll, never past :attr:`until`; raises once it has passed."""
        self.check()
        return max(0.0, min(self.poll_seconds, self.until - time.monotonic()))

    def check(self) -> None:
        """Raise that the job is still running once :attr:`until` has passed."""
        if time.monotonic() >= self.until:
            seconds = self.until - self.started
            message = MSG_OXYLABS_STILL_RUNNING.format(source=self.request.source, seconds=seconds, job_id=self.job_id)
            raise LiveDataJobError(self.provider, message, job_id=self.job_id)

    @contextmanager
    def naming(self, message: str) -> Iterator[None]:
        """Raise any failure inside as the job's error, saying ``message``, so the billed job's id is never lost.

        Whatever fails -- a status, the connection, an undecodable answer, a
        result with no fields -- the job was accepted, so the caller gets its
        id. A router error other than a provider's (a deadline, say) passes
        through. A poll is clamped to :attr:`until`, which stops short of the
        call's deadline, so a poll that timed out reads as the job's failure.
        """
        try:
            yield
        except LiveDataJobError:
            raise
        except WebRouterError as exc:
            if not isinstance(exc, ProviderError):
                raise
            self._raise(message, exc, exc.original)
        except Exception as exc:
            self._raise(message, exc, exc)

    def _raise(self, message: str, exc: Exception, original: BaseException | None) -> NoReturn:
        text = message.format(job_id=self.job_id, source=self.request.source, error=exc)
        raise LiveDataJobError(self.provider, text, job_id=self.job_id, original=original) from exc
