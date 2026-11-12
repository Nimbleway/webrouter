"""Exa's answers, over its Answer API.

:class:`ExaAnswerProvider` asks ``POST /answer`` for one answer with its
citations; :mod:`web_router.providers.exa`, which searches and extracts,
re-exports it. Install with the ``answer`` extra. Reads ``EXA_API_KEY`` unless
a key is passed explicitly.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import ENV_EXA_API_KEY, EXA_API_KEY_HEADER, EXA_BASE_URL, PROVIDER_EXA
from web_router.constants_exa import EXA_ANSWER_CITATION_TEXT_FIELD, EXA_ANSWER_PATH, EXA_COST_PATH
from web_router.providers._cost import stated_cost
from web_router.providers._http import HttpAnswerProvider
from web_router.providers._search_text import as_snippet
from web_router.providers._utils import build_payload
from web_router.providers.answer import cited_marker
from web_router.types import Citation
from web_router.types_answer import AnswerRequest, AnswerResponse

_MAPPED_ANSWER_FIELDS = frozenset({"url", "title", "text"})


class ExaAnswerProvider(HttpAnswerProvider):
    """Answers a question through Exa's Answer API, ``POST /answer``.

    The answer cites inline as ``[2]``, numbering ``citations`` from 1; a
    citation whose marker appears in the answer carries it in ``field``. A
    cited page's ``text``, when Exa returns it, is its snippet. ``requestId``
    is the request id, and ``costDollars`` -- the call's own stated cost --
    is ``cost_usd``, reported.

    No normalized option is mapped: Exa's Answer API documents no domain or
    date filter, and ``userLocation`` showed no clear effect live, so
    ``country`` is refused rather than sent to it.
    """

    name: ClassVar[str] = PROVIDER_EXA
    supported_parameters: ClassVar[frozenset[str]] = frozenset()

    base_url: ClassVar[str] = EXA_BASE_URL
    answer_path: ClassVar[str] = EXA_ANSWER_PATH
    env_api_key: ClassVar[str] = ENV_EXA_API_KEY
    api_key_header: ClassVar[str] = EXA_API_KEY_HEADER

    def _build_payload(self, request: AnswerRequest) -> dict[str, Any]:
        return build_payload(request, query=request.query)

    def _to_response(self, request: AnswerRequest, payload: dict[str, Any]) -> AnswerResponse:
        answer = payload.get("answer")
        text = answer if isinstance(answer, str) else ""
        return AnswerResponse(
            provider=self.name,
            query=request.query,
            answer=text,
            citations=[
                Citation(
                    url=cited.get("url", ""),
                    title=cited.get("title") or "",
                    snippet=as_snippet(cited.get(EXA_ANSWER_CITATION_TEXT_FIELD) or ""),
                    field=cited_marker(text, position),
                    extra={k: v for k, v in cited.items() if k not in _MAPPED_ANSWER_FIELDS},
                )
                for position, cited in enumerate(payload.get("citations") or [], start=1)
            ],
            request_id=payload.get("requestId"),
            raw=[payload],
            **stated_cost(payload, *EXA_COST_PATH),
        )
