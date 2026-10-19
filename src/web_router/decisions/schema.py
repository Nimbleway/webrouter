"""The locked decision schema: what any decider's answer, and the route built from it, must look like.

One record shape whichever model decided -- Jev, an OpenAI model, keyword rules
or a vendor's own -- so a measurement can compare deciders field for field.
The JSON Schema of :class:`Decision` is committed under
``tests/fixtures/decisions/`` and a test fails when the two drift apart: a
change here is deliberate, ships with a new fixture, and bumps
:data:`SCHEMA_VERSION`.

Checking never drops a problem silently. :func:`validate_answers` and
:func:`validate_decision` return every violation as a :class:`Violation`
record beside the parsed value, rather than raising or discarding.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from web_router.constants import (
    DECISION_CONFIDENCE_TOLERANCE,
    DECISION_PROBABILITY_TOLERANCE,
    DECISION_SCHEMA_VERSION,
    DECISION_VIOLATION_MSG_LIMIT,
    DECISION_VIOLATION_NOT_A_MAPPING,
    DECISION_VIOLATION_ROOT,
    DECISION_VIOLATION_VALUE_LIMIT,
    MSG_DECISION_BAD_CHOICE,
    MSG_DECISION_CHOICE_NOT_IN_PROBABILITIES,
    MSG_DECISION_CONFIDENCE_NOT_MIN,
    MSG_DECISION_CONFIDENCE_WITHOUT_LABELS,
    MSG_DECISION_NOT_A_MAPPING,
    MSG_DECISION_PROBABILITY_RANGE,
    MSG_DECISION_PROBABILITY_SUM,
    ROUTE_QUESTIONS,
)

if TYPE_CHECKING:
    from web_router.decisions.route import Route

SCHEMA_VERSION = DECISION_SCHEMA_VERSION

RouteMethod = Literal["search", "extract", "research", "browse"]
"""ROUTE_METHODS as a type; a test keeps the two equal."""

FallbackReason = Literal["decider_error", "low_confidence", "no_url", "no_backend_for_method"]
"""The four ROUTE_FALLBACK_* codes as a type; a test keeps the two equal."""


def _choices(question: str) -> list[str]:
    return list(ROUTE_QUESTIONS[question]["criteria"])


class _Locked(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _choice_enum(schema: dict[str, Any], cls: type[Any]) -> None:
    # The choices live in ROUTE_QUESTIONS; writing them into the JSON Schema puts them under the lock too.
    schema["properties"]["choice"]["enum"] = _choices(cls.question)


class Label(_Locked):
    """One question's answer: the pick, how sure the decider is, and its whole distribution, if it gave one."""

    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_extra=_choice_enum)

    question: ClassVar[str]

    choice: str
    confidence: float = Field(ge=0.0, le=1.0)
    probabilities: dict[str, float] = Field(default_factory=dict)

    @field_validator("choice")
    @classmethod
    def _choice_is_offered(cls, value: str) -> str:
        choices = _choices(cls.question)
        if value not in choices:
            raise ValueError(MSG_DECISION_BAD_CHOICE.format(question=cls.question, choice=value, choices=choices))
        return value

    @model_validator(mode="after")
    def _probabilities_are_a_distribution(self) -> Label:
        if not self.probabilities:
            return self
        outside = sorted(name for name, value in self.probabilities.items() if not 0.0 <= value <= 1.0)
        if outside:
            raise ValueError(MSG_DECISION_PROBABILITY_RANGE.format(names=outside))
        total = sum(self.probabilities.values())
        if abs(total - 1.0) > DECISION_PROBABILITY_TOLERANCE:
            raise ValueError(MSG_DECISION_PROBABILITY_SUM.format(total=total))
        if self.choice not in self.probabilities:
            raise ValueError(MSG_DECISION_CHOICE_NOT_IN_PROBABILITIES.format(choice=self.choice))
        return self


class ToolLabel(Label):
    question: ClassVar[str] = "tool"


class RecentLabel(Label):
    question: ClassVar[str] = "recent"


class CoverageLabel(Label):
    question: ClassVar[str] = "coverage"


class WellScopedLabel(Label):
    question: ClassVar[str] = "well_scoped"


class DecisionLabels(_Locked):
    """A decider's answer to every one of ROUTE_QUESTIONS: all four, and nothing else."""

    tool: ToolLabel
    recent: RecentLabel
    coverage: CoverageLabel
    well_scoped: WellScopedLabel

    @property
    def min_confidence(self) -> float:
        return min(self.tool.confidence, self.recent.confidence, self.coverage.confidence, self.well_scoped.confidence)


class DecisionRoute(_Locked):
    """What the labels turned into: the method, its chain in order, and why it is not the plain reading, if not."""

    method: RouteMethod
    providers: list[str] = Field(min_length=1)
    fallback: list[FallbackReason] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)


class Decision(_Locked):
    """One routing decision, in the locked shape every decider is compared in.

    ``labels`` and ``confidence`` are ``None`` when no decider answered: it
    failed (``route.fallback`` says ``decider_error``), or a given URL or
    schema made asking unnecessary. Otherwise ``confidence`` is the lowest
    label confidence, since the route is only as sure as its least sure label.
    """

    schema_version: Literal["decision-v1"] = "decision-v1"
    """Always DECISION_SCHEMA_VERSION; a test keeps the two equal."""
    labels: DecisionLabels | None
    route: DecisionRoute
    confidence: float | None = Field(ge=0.0, le=1.0, description="The minimum confidence over the four labels.")
    rationale: str = Field(min_length=1)
    decider_id: str = Field(min_length=1)
    decider_version: str = Field(min_length=1)
    latency_ms: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _confidence_is_the_least_sure_label(self) -> Decision:
        if (self.labels is None) != (self.confidence is None):
            raise ValueError(MSG_DECISION_CONFIDENCE_WITHOUT_LABELS)
        if self.labels is not None and self.confidence is not None:
            minimum = self.labels.min_confidence
            if abs(self.confidence - minimum) > DECISION_CONFIDENCE_TOLERANCE:
                raise ValueError(MSG_DECISION_CONFIDENCE_NOT_MIN.format(confidence=self.confidence, minimum=minimum))
        return self

    @classmethod
    def from_route(cls, route: Route, *, decider_id: str, decider_version: str) -> Decision:
        """The locked record of a :class:`~web_router.decisions.Route` from :meth:`~web_router.WebRouter.classify`.

        Raises:
            pydantic.ValidationError: if the route's labels or chain break the schema.
        """
        labels = DecisionLabels.model_validate(_answers_as_dicts(route.labels)) if route.labels else None
        return cls.model_validate(
            {
                "labels": labels,
                "route": {
                    "method": route.method,
                    "providers": route.providers,
                    "fallback": list(route.fallback),
                    "urls": list(route.urls),
                },
                "confidence": labels.min_confidence if labels is not None else None,
                "rationale": str(route),
                "decider_id": decider_id,
                "decider_version": decider_version,
                "latency_ms": max(0.0, route.latency_ms),
            }
        )


@dataclass(frozen=True)
class Violation:
    """One way an answer broke the schema: where, the offending value, which decider, and what is wrong."""

    field: str
    value: str
    decider: str
    msg: str
    type: str

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "value": self.value, "decider": self.decider, "msg": self.msg, "type": self.type}


def violations_from(error: ValidationError, decider: str) -> list[Violation]:
    """Every error in a pydantic ``ValidationError``, as violation records naming ``decider``."""
    return [
        Violation(
            field=".".join(map(str, item["loc"])) or DECISION_VIOLATION_ROOT,
            value=repr(item.get("input"))[:DECISION_VIOLATION_VALUE_LIMIT],
            decider=decider,
            msg=item["msg"][:DECISION_VIOLATION_MSG_LIMIT],
            type=item["type"],
        )
        for item in error.errors()
    ]


def validate_answers(answers: Any, decider: str) -> tuple[DecisionLabels | None, list[Violation]]:
    """Check a decider's raw ``decide()`` answer -- ``{question: Answer}`` -- against the schema.

    Returns the parsed labels and no violations, or ``None`` and every violation found.
    """
    if not isinstance(answers, Mapping):
        message = MSG_DECISION_NOT_A_MAPPING.format(kind=type(answers).__name__)
        return None, [
            Violation(
                field=DECISION_VIOLATION_ROOT,
                value=repr(answers)[:DECISION_VIOLATION_VALUE_LIMIT],
                decider=decider,
                msg=message[:DECISION_VIOLATION_MSG_LIMIT],
                type=DECISION_VIOLATION_NOT_A_MAPPING,
            )
        ]
    try:
        return DecisionLabels.model_validate(_answers_as_dicts(answers)), []
    except ValidationError as exc:
        return None, violations_from(exc, decider)


def validate_decision(data: Any, decider: str) -> tuple[Decision | None, list[Violation]]:
    """Check a whole decision record, as a mapping or a :class:`Decision`, against the schema."""
    try:
        return Decision.model_validate(data), []
    except ValidationError as exc:
        return None, violations_from(exc, decider)


def _answers_as_dicts(answers: Mapping[str, Any]) -> dict[str, Any]:
    # Duck-typed rather than isinstance(Answer): a vendor's own answer type with the same three fields is fine.
    return {
        name: {
            "choice": getattr(answer, "choice", None),
            "confidence": getattr(answer, "confidence", None),
            "probabilities": getattr(answer, "probabilities", None) or {},
        }
        for name, answer in answers.items()
    }
