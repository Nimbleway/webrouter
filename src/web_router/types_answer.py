"""The answer method's request and response.

Kept apart so ``types.py`` stops growing. ``web_router`` exports them too; ``types.py``
imports nothing of ours, so this module imports it, never the other way round.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from web_router.types import META_DESCRIPTION, Citation, NormalizedRequest, ResearchOutcome, ResponseMeta

__all__ = ["ANSWER_ALWAYS_SENT", "ANSWER_PARAMETERS", "AnswerRequest", "AnswerResponse", "Recency"]

Recency = Literal["day", "week", "month", "year"]
"""How recent a source must be: published within the last day, week, month or year."""


class AnswerRequest(NormalizedRequest):
    """A normalized answer request: one question, answered in one call.

    Only :attr:`query` is required. Fields left as ``None`` are omitted from the
    provider call, as for a search.
    """

    always_sent: ClassVar[frozenset[str]] = frozenset({"query", "provider_options"})

    query: Annotated[str, Field(min_length=1, description="The question to answer.")]
    include_domains: list[str] | None = Field(default=None, description="Restrict sources to these domains.")
    exclude_domains: list[str] | None = Field(default=None, description="Keep these domains out of the sources.")
    country: str | None = Field(default=None, description="Country code for geo-targeting, e.g. 'US'.")
    locale: str | None = Field(default=None, description="Language code, e.g. 'en'.")
    recency: Recency | None = Field(default=None, description="Only sources from the last day, week, month or year.")


ANSWER_ALWAYS_SENT = AnswerRequest.always_sent
"""Fields every answer provider handles itself: the query, and the untranslated passthrough."""

ANSWER_PARAMETERS: frozenset[str] = AnswerRequest.normalized_parameters()
"""Every optional parameter an answer provider may declare support for."""


class AnswerResponse(BaseModel):
    """A written answer and the sources it rests on, tagged with the provider that wrote it.

    The backend writes the answer; nothing here composes or edits it. An empty
    :attr:`answer` is returned as it came, never raised, so a caller can tell
    "the backend had nothing" from a failure and still read :attr:`raw`.
    """

    # Unknown keys are a provider bug: the untouched payloads belong in `raw`.
    model_config = ConfigDict(extra="forbid")

    provider: str
    query: str
    answer: str | None = Field(
        default=None,
        description="The written answer. Every backend sets it, to '' when it wrote nothing.",
    )
    citations: list[Citation] = Field(default_factory=list)
    outcome: ResearchOutcome = Field(
        default="complete",
        description="Whether the backend finished the answer or said it stopped early.",
    )
    request_id: str | None = Field(default=None, description="The backend's own id for the call, when it gives one.")
    meta: ResponseMeta | None = Field(default=None, description=META_DESCRIPTION)
    raw: list[dict[str, Any]] | None = Field(
        default=None,
        description="The provider's untouched payloads, one per call the answer took.",
    )
