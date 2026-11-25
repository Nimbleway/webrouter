"""What a status tool waits on: a research run or a browse task, for as long as the tool that started it waits.

``research`` and ``browse`` wait for their job before handing back a handle, and ``research_status`` and
``browse_status`` wait the same way, so the model never has to pace its polls: a call answers the moment the job
stops, and otherwise says it is still going once its wait runs out.
"""

from __future__ import annotations

from typing import overload

from web_router.constants import (
    MCP_TOOL_BROWSE,
    MCP_TOOL_BROWSE_STATUS,
    MCP_TOOL_RESEARCH,
    MCP_TOOL_RESEARCH_STATUS,
    MCP_TOOL_WAITS,
)
from web_router.exceptions import BrowseTimeoutError, ProviderError, ResearchTimeoutError
from web_router.providers.base import BrowseJob, ResearchJob
from web_router.types import ResearchProgress
from web_router.types_browse import BrowseProgress


def tool_waits(research_wait: float, browse_wait: float) -> dict[str, float]:
    """Seconds each tool that waits on a job waits: a status tool as long as the tool that started its job."""
    return {
        **MCP_TOOL_WAITS,
        MCP_TOOL_RESEARCH: research_wait,
        MCP_TOOL_RESEARCH_STATUS: research_wait,
        MCP_TOOL_BROWSE: browse_wait,
        MCP_TOOL_BROWSE_STATUS: browse_wait,
    }


@overload
async def settle(job: ResearchJob, wait: float) -> ResearchProgress: ...
@overload
async def settle(job: BrowseJob, wait: float) -> BrowseProgress: ...


async def settle(job: ResearchJob | BrowseJob, wait: float) -> ResearchProgress | BrowseProgress:
    """``job``'s progress after waiting up to ``wait`` seconds for it to stop.

    A job that failed or was cancelled has stopped too: :meth:`~web_router.providers.base.Job.aresult` raises for
    it, and its last progress, the reason included, is what the status tool reports. A poll that failed while the
    job is still going -- an auth error, an unrecognized status -- is raised as it is, as a single poll's was.
    """
    try:
        await job.aresult(timeout=wait)
    except (ResearchTimeoutError, BrowseTimeoutError):
        pass
    except ProviderError:
        if not job.progress.is_terminal:
            raise
    return job.progress
