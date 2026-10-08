"""What an accuracy evaluation reports.

Every number here is either measured -- a latency, a count -- or a judgment a
named model made, and the report says which model. Nothing is a fact the
search backend stated about itself, and nothing ranks the backends.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from web_router.constants import MSG_EVALS_ROW_OUTCOME

Grade = Literal["correct", "incorrect", "not_attempted"]
"""The SimpleQA grades: the answer contains the gold, contradicts it, or does neither."""

FailedAt = Literal["search", "synthesis", "grading"]
"""Where a row stopped: the backend's call, or one of the judge's two."""


class EvaluationRow(BaseModel):
    """One provider's answer to one question, and the grade it got.

    A row whose call or judging failed carries the error under :attr:`error`
    and where it happened under :attr:`failed_at`; such a row is left out of
    accuracy rather than scored wrong, and counted in the provider's failure
    rate. A row the backend served but whose answer was ``not_attempted`` is
    scored as a miss: the call reached the backend, so failing to answer
    costs what answering wrong does.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    provider: str
    question: str
    target: str = Field(description="The gold answer the dataset gave.")
    latency: float = Field(ge=0.0, description="Wall-clock seconds of the search call alone; judging is not counted.")
    withheld: int = Field(
        default=0,
        ge=0,
        description="Results kept from the judge because their page may hold the dataset's own answers.",
    )
    answer: str | None = Field(default=None, description="What the judge synthesized from the results.")
    grade: Grade | None = Field(default=None, description="The judge's grade of that answer against the gold.")
    error: Exception | None = Field(default=None, description="What failed, when something did.")
    failed_at: FailedAt | None = None

    @field_serializer("error")
    def _serialize_error(self, error: Exception | None) -> str | None:
        return None if error is None else str(error)

    @model_validator(mode="after")
    def _one_outcome(self) -> EvaluationRow:
        if (self.grade is None) == (self.error is None) or (self.error is None) != (self.failed_at is None):
            raise ValueError(MSG_EVALS_ROW_OUTCOME)
        return self

    @property
    def served(self) -> bool:
        """Whether the backend answered and the judge graded it -- the rows accuracy is computed over."""
        return self.grade is not None

    @property
    def correct(self) -> bool:
        return self.grade == "correct"


class ProviderScore(BaseModel):
    """One provider's column in the report."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    questions: int = Field(description="How many questions were asked of it.")
    served: int = Field(description="How many it answered and the judge graded.")
    failed: int = Field(description="How many rows stopped on an error, at any stage.")
    correct: int
    incorrect: int
    not_attempted: int
    accuracy: float | None = Field(
        description="correct / served, so a served question the answer missed is a miss; None when nothing was served."
    )
    accuracy_when_attempted: float | None = Field(
        description="correct / (correct + incorrect), the openai/simple-evals figure, which leaves not_attempted out."
    )
    failure_rate: float = Field(description="failed / questions.")
    latency_p50: float | None = Field(description="Median search latency over served rows, in seconds.")
    latency_p95: float | None


class EvaluationReport(BaseModel):
    """A dataset of questions through several providers, each answer judged: rows, and a score per provider.

    :attr:`judge` names the model that synthesized and graded, because the
    accuracy column is its judgment and comparable only with figures the same
    judge produced.
    """

    model_config = ConfigDict(extra="forbid")

    dataset: str
    judge: str
    rows: list[EvaluationRow] = Field(default_factory=list)
    scores: list[ProviderScore] = Field(default_factory=list)

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, provider: str) -> ProviderScore:
        """The score for ``provider``."""
        for score in self.scores:
            if score.provider == provider:
                return score
        raise KeyError(provider)
