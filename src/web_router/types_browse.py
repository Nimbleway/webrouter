"""A browse task's progress: where it stands, and its answer once it has one.

Kept apart so ``types.py`` stops growing. ``web_router`` exports it too; ``types.py``
never imports this module, so this module imports it, never the other way round.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from web_router.types import BrowseResponse, JobState

__all__ = ["BrowseProgress"]


class BrowseProgress(BaseModel):
    """Where one browse task stands, and its answer once it has one."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(description="The backend's id for this task, which is what a later poll is keyed on.")
    state: JobState
    response: BrowseResponse | None = Field(
        default=None,
        description="The answer. Set exactly when `state` is 'completed'.",
    )
    detail: str | None = Field(
        default=None,
        description="Whatever the backend said about this state -- an error message, a progress note.",
    )

    @property
    def is_terminal(self) -> bool:
        """Whether this task has stopped, one way or another."""
        return self.state not in ("queued", "running")
