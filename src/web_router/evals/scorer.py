"""Scoring an answer that has no gold one: a :class:`Scorer` reads the question, the answer and its sources.

:class:`~web_router.evals.Judge` grades an answer against a known one. Many
questions worth asking have none -- a research report, a comparison, this
week's news -- and a scorer rates those instead, on dimensions it names
(factual accuracy, coverage, ...), into a :class:`Scorecard`. Its scores are its
own judgment and compare only with the same scorer's, so the card names it,
version included.

A scorer that checks claims on the web brings its own search: one that searched
through the backend whose answer it rates would be checking that backend
against itself.

web-router ships the interface, not a scorer: subclass :class:`Scorer` to plug
one in.
"""

from __future__ import annotations

import asyncio
import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from web_router.constants_evals import MSG_SCORE_BAD_SCALE, MSG_SCORE_OUT_OF_SCALE, MSG_SCORE_UNKNOWN_REASON
from web_router.types_citation import Citation


class Scorecard(BaseModel):
    """One answer's scores, the scorer that gave them, and its reasons and record of the run.

    ``dimensions`` and ``overall`` are what compare across answers; ``reasons``
    say why, for a reader; ``details`` hold whatever else the scorer reports
    (tokens, cost, searches, version flags) without web-router naming it.
    """

    model_config = ConfigDict(extra="forbid")

    scorer: str = Field(description="The scorer's label, its version included: compare only with the same scorer's.")
    scale: tuple[float, float] = Field(description="The lowest and highest score any dimension, and overall, can get.")
    dimensions: dict[str, float | None] = Field(
        description="Each dimension's score; None where it did not apply or the scorer failed on it."
    )
    overall: float | None = Field(description="The scorer's one number for the answer; None when it gave none.")
    reasons: dict[str, str] = Field(
        default_factory=dict,
        description="Why each dimension got its score, or why it got none, in the scorer's words; keyed as dimensions.",
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "The scorer's own record of the run, as JSON-ready values: what it spent, what it searched, whether "
            "the card compares with its published scores. Keys are the scorer's to define."
        ),
    )

    @model_validator(mode="after")
    def _reasons_name_dimensions(self) -> Scorecard:
        unknown = sorted(set(self.reasons) - set(self.dimensions))
        if unknown:
            raise ValueError(MSG_SCORE_UNKNOWN_REASON.format(names=", ".join(unknown)))
        return self

    @model_validator(mode="after")
    def _within_scale(self) -> Scorecard:
        low, high = self.scale
        if not (math.isfinite(low) and math.isfinite(high) and low < high):
            raise ValueError(MSG_SCORE_BAD_SCALE.format(scale=self.scale))
        for name, value in [*self.dimensions.items(), ("overall", self.overall)]:
            if value is not None and not low <= value <= high:
                raise ValueError(MSG_SCORE_OUT_OF_SCALE.format(name=name, value=value, low=low, high=high))
        return self


class Scorer(ABC):
    """Rates an answer to a question from the answer and its sources alone.

    Implement :meth:`score`, and set :attr:`name` -- or override
    :attr:`label` -- to what a scorecard should record. :meth:`ascore` runs
    :meth:`score` in a thread unless overridden.
    """

    name: ClassVar[str] = "scorer"

    @property
    def label(self) -> str:
        """What a scorecard records under ``scorer``."""
        return self.name

    def check(self) -> None:  # noqa: B027
        """Raise now what the first call would raise; a scorer with nothing to check does nothing."""

    @abstractmethod
    def score(self, question: str, answer: str, citations: Sequence[Citation] = ()) -> Scorecard: ...

    async def ascore(self, question: str, answer: str, citations: Sequence[Citation] = ()) -> Scorecard:
        return await asyncio.to_thread(self.score, question, answer, citations)
