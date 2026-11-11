"""Parallel providers, over the Search API, the Task API and the Chat API.

Talks to the REST endpoints directly rather than through ``parallel-web``:
web-router uses a handful of fields of these APIs, which an SDK dependency would
not simplify. Install with the ``search``, ``research`` or ``answer`` extra. Reads ``PARALLEL_API_KEY`` unless a key
is passed explicitly.

Search lives in :mod:`web_router.providers.parallel_search` and research in
:mod:`web_router.providers.parallel_research`; their providers are imported
here too, so ``ParallelProvider`` and ``ParallelResearchProvider`` keep their
import paths.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import ENV_PARALLEL_API_KEY, PARALLEL_BASE_URL, PROVIDER_PARALLEL
from web_router.constants_parallel import (
    PARALLEL_ANSWER_API_KEY_HEADER,
    PARALLEL_ANSWER_API_KEY_PREFIX,
    PARALLEL_ANSWER_BASIS_EXTRA,
    PARALLEL_ANSWER_MODEL,
    PARALLEL_ANSWER_PATH,
    PARALLEL_ANSWER_ROLE,
    PARALLEL_ANSWER_TRUNCATED,
    PARALLEL_ANSWER_WHOLE_FIELD,
)
from web_router.providers._http import HttpAnswerProvider
from web_router.providers._utils import build_payload
from web_router.providers.parallel_research import ParallelResearchProvider as ParallelResearchProvider
from web_router.providers.parallel_research import _citations_from_basis
from web_router.providers.parallel_search import ParallelProvider as ParallelProvider
from web_router.types_answer import AnswerRequest, AnswerResponse


class ParallelAnswerProvider(HttpAnswerProvider):
    """Answers a question through Parallel's Chat API (beta), with the ``lite`` model.

    One ``POST /v1beta/chat/completions``: the answer is the message content,
    and its sources are the top-level ``basis`` -- per answer field, the
    citations behind it with their excerpts, a ``reasoning`` and a
    ``confidence``. A citation backing the answer as a whole has
    ``field=None``; the basis's ``confidence`` rides in its ``extra``, and the
    reasoning stays in ``raw``. A ``finish_reason`` of ``length`` is
    ``partial``.

    The Chat API takes no source filters, so every option is refused. Another
    model goes through ``provider_options``; ``speed`` returns no ``basis``.
    """

    name: ClassVar[str] = PROVIDER_PARALLEL
    supported_parameters: ClassVar[frozenset[str]] = frozenset()

    base_url: ClassVar[str] = PARALLEL_BASE_URL
    answer_path: ClassVar[str] = PARALLEL_ANSWER_PATH
    env_api_key: ClassVar[str] = ENV_PARALLEL_API_KEY
    api_key_header: ClassVar[str] = PARALLEL_ANSWER_API_KEY_HEADER
    api_key_prefix: ClassVar[str] = PARALLEL_ANSWER_API_KEY_PREFIX

    def _build_payload(self, request: AnswerRequest) -> dict[str, Any]:
        return build_payload(
            request,
            model=PARALLEL_ANSWER_MODEL,
            messages=[{"role": PARALLEL_ANSWER_ROLE, "content": request.query}],
        )

    def _to_response(self, request: AnswerRequest, payload: dict[str, Any]) -> AnswerResponse:
        choice = (payload.get("choices") or [{}])[0]
        return AnswerResponse(
            provider=self.name,
            query=request.query,
            answer=(choice.get("message") or {}).get("content") or "",
            citations=_citations_from_basis(
                payload.get("basis") or [],
                whole_field=PARALLEL_ANSWER_WHOLE_FIELD,
                basis_extra=PARALLEL_ANSWER_BASIS_EXTRA,
            ),
            outcome="partial" if choice.get("finish_reason") == PARALLEL_ANSWER_TRUNCATED else "complete",
            request_id=payload.get("id"),
            raw=[payload],
        )
