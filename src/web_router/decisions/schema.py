"""The locked decision schema: what any decider's answer, and the route built from it, must look like.

One record shape whichever model decided -- Jev, a chat model, keyword rules
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

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from web_router.constants import (
    DECISION_CONFIDENCE_DIGITS,
    DECISION_CONFIDENCE_TOLERANCE,
    DECISION_ERROR_EXCEPTION,
    DECISION_ERROR_KIND_BY_EXCEPTION,
    DECISION_LATENCY_DIGITS,
    DECISION_PROBABILITY_TOLERANCE,
    DECISION_ROUTE_LABELS,
    DECISION_SCHEMA_VERSION,
    DECISION_STATUS_ANSWERED,
    DECISION_STATUS_FAILED,
    DECISION_STATUS_NOT_ASKED,
    DECISION_VIOLATION_MSG_LIMIT,
    DECISION_VIOLATION_NOT_A_MAPPING,
    DECISION_VIOLATION_ROOT,
    DECISION_VIOLATION_VALUE_LIMIT,
    MSG_DECISION_BAD_CHOICE,
    MSG_DECISION_CHOICE_NOT_IN_PROBABILITIES,
    MSG_DECISION_CONFIDENCE_NOT_MIN,
    MSG_DECISION_ERROR_STATUS,
    MSG_DECISION_FAILED_FALLBACK,
    MSG_DECISION_LABELS_STATUS,
    MSG_DECISION_NOT_A_MAPPING,
    MSG_DECISION_PROBABILITY_RANGE,
    MSG_DECISION_PROBABILITY_SUM,
    ROUTE_FALLBACK_DECIDER_ERROR,
    ROUTE_FALLBACK_LOW_CONFIDENCE,
    ROUTE_QUESTIONS,
)

if TYPE_CHECKING:
    from web_router.decisions.route import Route

SCHEMA_VERSION = DECISION_SCHEMA_VERSION

RouteMethod = Literal["search", "extract", "research", "browse"]
"""ROUTE_METHODS as a type; a test keeps the two equal."""

FallbackReason = Literal["decider_error", "low_confidence", "no_url", "no_backend_for_method"]
"""The four ROUTE_FALLBACK_* codes as a type; a test keeps the two equal."""

DeciderStatus = Literal["answered", "not_asked", "failed"]
"""The DECISION_STATUS_* values as a type; a test keeps the two equal."""

ErrorKind = Literal["timeout", "http_error", "invalid_answer", "exception"]
"""The DECISION_ERROR_* kinds as a type; a test keeps the two equal."""


def _choices(question: str) -> list[str]:
    return list(ROUTE_QUESTIONS[question]["criteria"])


class _Locked(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _choice_enum(schema: dict[str, Any], cls: type[Any]) -> None:
    # The choices live in ROUTE_QUESTIONS; writing them into the JSON Schema puts them under the lock too.
    schema["properties"]["choice"]["enum"] = _choices(cls.question)


class Label(_Locked):
    """One question's answer: the pick, how sure the decider is, and its whole distribution, if it gave one.

    The built-in deciders report ``confidence`` on one scale, how far the pick
    stands above an even guess: Jev reports it itself (and may leave
    ``probabilities`` empty), the llm decider computes it from the model's own
    distribution -- self-reported, not calibrated -- and the keyword rules'
    is a fixed constant, not measured. Compare it across deciders with care.
    """

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
    """A decider's answer to ROUTE_QUESTIONS: every question named, and nothing else.

    A decider's raw answer must give all four. In a decision record a label
    is ``None`` when the decider gave none for it, or -- under a non-strict
    :class:`~web_router.decisions.ValidatedDecider` -- gave one that broke the
    schema; the route used that label's default either way.
    """

    tool: ToolLabel | None
    recent: RecentLabel | None
    coverage: CoverageLabel | None
    well_scoped: WellScopedLabel | None

    @property
    def route_confidence(self) -> float | None:
        """The lowest confidence over the labels a route is built from (tool, recent, coverage), if any were given."""
        given = [label.confidence for name in DECISION_ROUTE_LABELS if (label := getattr(self, name)) is not None]
        return min(given) if given else None


class DecisionRoute(_Locked):
    """What the labels turned into: the method, its chain in order, and why it is not the plain reading, if not."""

    method: RouteMethod
    tool: RouteMethod
    """The method asked for before any downgrade (#11's ``Route.requested``): the raw tool label, even an unsure one,
    or extract for a given URL and research for a given schema. Differs from ``method`` exactly when a fallback moved
    the route."""
    providers: list[str] = Field(min_length=1)
    fallback: list[FallbackReason] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)


class DecisionError(_Locked):
    """Why the decider gave no answer, for a route that took the ``decider_error`` fallback."""

    kind: ErrorKind
    message: str = Field(min_length=1)


class Violation(_Locked):
    """One way an answer broke the schema: where, the offending value, which decider, and what is wrong."""

    field: str
    value: str
    decider: str
    msg: str
    type: str

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "value": self.value, "decider": self.decider, "msg": self.msg, "type": self.type}


class Decision(_Locked):
    """One routing decision, in the locked shape every decider is compared in.

    ``decider_status`` says what happened to the decider: ``answered``,
    ``not_asked`` (a given URL or schema settled the method), or ``failed``
    (``route.fallback`` has ``decider_error`` and ``error`` says why, when the
    route came through :class:`~web_router.decisions.ValidatedDecider`).
    ``labels`` is set exactly when it answered.

    ``confidence`` is the lowest confidence over tool, recent and coverage --
    the labels a route is built from -- so it is ``None`` when none of them was
    given. well_scoped keeps its own label confidence and is left out, because
    it moves no route. Read the vendor note on :class:`Label` before comparing
    confidences across deciders.

    A record built by :meth:`from_route` rounds confidences and probabilities
    to 4 places and ``latency_ms`` to 0.1 ms.
    """

    schema_version: Literal["decision-v2"] = "decision-v2"
    """Always DECISION_SCHEMA_VERSION; a test keeps the two equal."""
    decider_status: DeciderStatus
    labels: DecisionLabels | None
    route: DecisionRoute
    confidence: float | None = Field(
        ge=0.0, le=1.0, description="The minimum confidence over tool, recent and coverage."
    )
    rationale: str = Field(min_length=1)
    decider_id: str = Field(min_length=1)
    decider_version: str = Field(min_length=1)
    latency_ms: float = Field(ge=0.0)
    timeout_s: float | None = Field(default=None, gt=0.0, description="The decider's time budget, if it has one.")
    error: DecisionError | None = None
    violations: list[Violation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _the_parts_agree(self) -> Decision:
        if (self.labels is not None) != (self.decider_status == DECISION_STATUS_ANSWERED):
            raise ValueError(MSG_DECISION_LABELS_STATUS)
        if self.error is not None and self.decider_status != DECISION_STATUS_FAILED:
            raise ValueError(MSG_DECISION_ERROR_STATUS)
        if self.decider_status == DECISION_STATUS_FAILED and ROUTE_FALLBACK_DECIDER_ERROR not in self.route.fallback:
            raise ValueError(MSG_DECISION_FAILED_FALLBACK)
        minimum = self.labels.route_confidence if self.labels is not None else None
        if (minimum is None) != (self.confidence is None) or (
            minimum is not None
            and self.confidence is not None
            and abs(self.confidence - minimum) > DECISION_CONFIDENCE_TOLERANCE
        ):
            raise ValueError(MSG_DECISION_CONFIDENCE_NOT_MIN.format(confidence=self.confidence, minimum=minimum))
        return self

    @classmethod
    def from_route(
        cls,
        route: Route,
        *,
        decider_id: str,
        decider_version: str,
        timeout_s: float | None = None,
        error: DecisionError | None = None,
        violations: Sequence[Violation] = (),
    ) -> Decision:
        """The locked record of a :class:`~web_router.decisions.Route` from :meth:`~web_router.WebRouter.classify`.

        Raises:
            pydantic.ValidationError: if the route's labels or chain break the schema.
        """
        status = route_status(route)
        labels = None
        if status == DECISION_STATUS_ANSWERED:
            given = _answers_as_dicts(route.labels, digits=DECISION_CONFIDENCE_DIGITS)
            labels = DecisionLabels.model_validate({name: given.get(name) for name in ROUTE_QUESTIONS})
        return cls.model_validate(
            {
                "decider_status": status,
                "labels": labels,
                "route": {
                    "method": route.method,
                    "tool": route.requested or route.method,
                    "providers": route.providers,
                    "fallback": list(route.fallback),
                    "urls": list(route.urls),
                },
                "confidence": labels.route_confidence if labels is not None else None,
                "rationale": str(route),
                "decider_id": decider_id,
                "decider_version": decider_version,
                "latency_ms": round(max(0.0, route.latency_ms), DECISION_LATENCY_DIGITS),
                "timeout_s": timeout_s,
                "error": error if status == DECISION_STATUS_FAILED else None,
                "violations": list(violations),
            }
        )


def route_status(route: Route) -> str:
    """Whether the decider answered, failed, or was not asked for ``route``, read from what classify recorded."""
    if route.labels:
        return DECISION_STATUS_ANSWERED
    if ROUTE_FALLBACK_DECIDER_ERROR in route.fallback:
        return DECISION_STATUS_FAILED
    # Asked, but nothing usable came back: classify defaulted every label and said low_confidence.
    if ROUTE_FALLBACK_LOW_CONFIDENCE in route.fallback:
        return DECISION_STATUS_ANSWERED
    return DECISION_STATUS_NOT_ASKED


def error_kind(exc: BaseException) -> str:
    """The kind of a decider failure, from the exception or, for a ProviderError, the one it wraps."""
    original = getattr(exc, "original", None) or exc.__cause__ or exc
    names = {cls.__name__ for cls in type(original).__mro__}
    for kind, markers in DECISION_ERROR_KIND_BY_EXCEPTION:
        if names.intersection(markers):
            return kind
    return DECISION_ERROR_EXCEPTION


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


def _answers_as_dicts(answers: Mapping[str, Any], digits: int | None = None) -> dict[str, Any]:
    # Duck-typed rather than isinstance(Answer): a vendor's own answer type with the same three fields is fine.
    def rounded(value: Any) -> Any:
        return round(value, digits) if digits is not None and isinstance(value, float) else value

    def distribution(answer: Any) -> Any:
        probabilities = getattr(answer, "probabilities", None) or {}
        # Anything but a mapping is left as it came, for the schema to report rather than for this to crash on.
        return (
            {k: rounded(v) for k, v in probabilities.items()} if isinstance(probabilities, Mapping) else probabilities
        )

    return {
        name: {
            "choice": getattr(answer, "choice", None),
            "confidence": rounded(getattr(answer, "confidence", None)),
            "probabilities": distribution(answer),
        }
        for name, answer in answers.items()
    }
