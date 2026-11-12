"""Waiting on a live data run a vendor took as a job: when to poll, when to give up, and the errors that name it.

Oxylabs' job-only scrapers and Bright Data's snapshots both go on, billed,
after the call that started them, so every error past that point is a
:class:`~web_router.exceptions_live_data.LiveDataJobError` naming the job. A
vendor's subclass of :class:`JobWait` says where the job is read, when it is
done and what "still running" says; the provider owns the HTTP and the
sleeping, sync or async.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, ClassVar, NoReturn

from web_router.constants_live_data import LIVE_DATA_JOB_DEADLINE_MARGIN
from web_router.exceptions import ProviderError, WebRouterError
from web_router.exceptions_live_data import LiveDataJobError
from web_router.providers._deadline import current_deadline
from web_router.types_live_data import LiveDataRequest


def wait_until(wait_seconds: float) -> float:
    """When a wait gives up: after ``wait_seconds``, or just before the call's deadline when that is sooner.

    Stopping short of the call's deadline is what keeps the job's id: at the
    deadline itself the router raises its own error, which names no job.
    """
    until = time.monotonic() + wait_seconds
    call = current_deadline()
    return until if call is None else min(until, call.at - LIVE_DATA_JOB_DEADLINE_MARGIN)


@dataclass
class JobWait:
    """One job being waited on, up to :attr:`until`, polled every :attr:`poll_seconds`."""

    still_running: ClassVar[str]
    """The message once the wait runs out, formatted with ``source``, ``seconds`` and ``job_id``."""

    provider: str
    request: LiveDataRequest
    job_id: str
    until: float
    poll_seconds: float
    started: float = field(default_factory=time.monotonic)

    @property
    def path(self) -> str:
        """Where the job's state is read."""
        raise NotImplementedError

    def done(self, payload: Any) -> bool:
        """Whether the job is done, from the state read at :attr:`path`; raises when it never will be."""
        raise NotImplementedError

    def pause(self) -> float:
        """Seconds to sleep before the next poll, never past :attr:`until`; raises once it has passed."""
        self.check()
        return max(0.0, min(self.poll_seconds, self.until - time.monotonic()))

    def check(self) -> None:
        """Raise that the job is still running once :attr:`until` has passed."""
        if time.monotonic() >= self.until:
            seconds = self.until - self.started
            message = self.still_running.format(source=self.request.source, seconds=seconds, job_id=self.job_id)
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
