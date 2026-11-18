"""A source an answer rests on: :class:`Citation`, shared by research and answer.

Apart from :mod:`web_router.types` so that file stops growing; it re-exports it,
and this imports nothing of ours.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Citation(BaseModel):
    """A source the answer rests on."""

    # Unknown keys are a provider bug: anything unmapped belongs in `extra`.
    model_config = ConfigDict(extra="forbid")

    url: str
    title: str = Field(default="", description="The cited page's title, when the provider returned one.")
    snippet: str = Field(default="", description="The passage the backend quoted from the page, when it quoted one.")
    field: str | None = Field(
        default=None,
        description=(
            "Which part of the answer this source backs -- a JSON path into `structured`, or a marker "
            "in `answer` (`[2]`, or on You.com the whole group that names it, `[[1, 2]]`) -- when the "
            "backend attributes per field. `None` when it ties the source to no part of it."
        ),
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific fields with no normalized equivalent.",
    )
