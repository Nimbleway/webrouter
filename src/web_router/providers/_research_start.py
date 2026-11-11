"""A research start whose run was created hands its job back, even when reading the answer then fails.

A backend that can finish a short run inside the call that starts it reads the
answer there too. That read is a second request, and the run was created and
billed before it: raising its failure would read, to ``auto``, as a run that
never started, and ``auto`` would start a second one. So the start reports the
run running instead, and the next poll reads the answer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from web_router.constants_research import MSG_RESEARCH_RESULT_UNFETCHED
from web_router.types import ResearchProgress

_Errors = tuple[type[BaseException], ...]


def read_or_running(read: Callable[[], ResearchProgress], job_id: str, errors: _Errors) -> ResearchProgress:
    """``read()``; a running progress for ``job_id`` when it fails with one of ``errors``."""
    try:
        return read()
    except errors as exc:
        return _running(job_id, exc)


async def aread_or_running(
    read: Callable[[], Awaitable[ResearchProgress]], job_id: str, errors: _Errors
) -> ResearchProgress:
    """Async counterpart of :func:`read_or_running`."""
    try:
        return await read()
    except errors as exc:
        return _running(job_id, exc)


def _running(job_id: str, error: BaseException) -> ResearchProgress:
    return ResearchProgress(job_id=job_id, state="running", detail=MSG_RESEARCH_RESULT_UNFETCHED.format(error=error))
