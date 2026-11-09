"""You.com research, over the Research API.

:class:`YouResearchProvider` runs research as a background task and polls it;
:mod:`web_router.providers.you`, which searches, re-exports it. Install with
the ``research`` extra. Reads ``YOU_API_KEY`` unless a key is passed
explicitly.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from web_router.constants import ENV_YOU_API_KEY, PROVIDER_YOU, YOU_API_KEY_HEADER
from web_router.constants_you import (
    YOU_RESEARCH_BACKGROUND,
    YOU_RESEARCH_BASE_URL,
    YOU_RESEARCH_EFFORTS,
    YOU_RESEARCH_PATH,
    YOU_RESEARCH_STATES,
    YOU_RESEARCH_STATUS_PATH,
)
from web_router.providers._http import HttpPolledResearchProvider
from web_router.providers._utils import build_payload, mapped
from web_router.providers.you_answer import YouAnswerProvider as YouAnswerProvider
from web_router.types import Citation, ResearchRequest, ResearchResponse, ResearchState

_MAPPED_SOURCE_FIELDS = frozenset({"url", "title", "snippets"})


class YouResearchProvider(HttpPolledResearchProvider):
    """Runs agentic research as a background task on the You.com Research API.

    ``POST /v1/research`` with ``background`` returns the task ``queued`` under
    ``task_id``, and ``GET /v1/research/{task_id}`` reports it, under ``id``,
    through ``running`` to ``completed`` -- verified live on 2026-10-16. The
    answer is ``result.output.content``, markdown with numbered citation
    markers, and its sources are ``result.output.sources``.

    ``effort`` maps onto ``research_effort``. You.com offers no way to stop a
    task, so :meth:`cancel` reports ``False``. Its API reports no early stop, so
    every finished run is ``complete``. Structured output is not mapped until a
    live run has checked it, so ``output_schema`` raises rather than coming back
    as prose, and a schema sent through ``provider_options`` leaves its result in
    ``raw``.
    """

    name: ClassVar[str] = PROVIDER_YOU
    supported_parameters: ClassVar[frozenset[str]] = frozenset({"effort"})

    base_url: ClassVar[str] = YOU_RESEARCH_BASE_URL
    env_api_key: ClassVar[str] = ENV_YOU_API_KEY
    api_key_header: ClassVar[str] = YOU_API_KEY_HEADER

    research_path: ClassVar[str] = YOU_RESEARCH_PATH
    status_path: ClassVar[str] = YOU_RESEARCH_STATUS_PATH
    research_states: ClassVar[Mapping[str, ResearchState]] = YOU_RESEARCH_STATES
    # The start call names the task `task_id`; the status call names it `id`.
    job_id_fields: ClassVar[tuple[str, ...]] = ("task_id", "id")

    def _build_payload(self, request: ResearchRequest) -> dict[str, Any]:
        effort = mapped(YOU_RESEARCH_EFFORTS, request.effort)
        return build_payload(request, input=request.query, research_effort=effort, background=YOU_RESEARCH_BACKGROUND)

    def _to_response(self, request: ResearchRequest, payload: dict[str, Any]) -> ResearchResponse:
        output = (payload.get("result") or {}).get("output") or {}
        content = output.get("content")
        return ResearchResponse(
            provider=self.name,
            query=request.query,
            # Only prose is read: a schema sent through `provider_options` comes
            # back in a shape no live run has checked, so it stays in `raw`.
            answer=content if isinstance(content, str) and content else None,
            citations=[self._to_citation(source) for source in output.get("sources") or []],
            request_id=payload.get("id") or payload.get("task_id"),
            raw=[payload],
        )

    @staticmethod
    def _to_citation(source: dict[str, Any]) -> Citation:
        return Citation(
            url=source.get("url", ""),
            title=source.get("title") or "",
            snippet="\n\n".join(snippet for snippet in source.get("snippets") or [] if snippet),
            extra={key: value for key, value in source.items() if key not in _MAPPED_SOURCE_FIELDS},
        )
