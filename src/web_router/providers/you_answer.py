"""You.com answers, over the Answer API.

:class:`YouAnswerProvider` answers one question in one call;
:mod:`web_router.providers.you`, which searches, re-exports it. Install with
the ``answer`` extra. Reads ``YOU_API_KEY`` unless a key is passed explicitly.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import ENV_YOU_API_KEY, PROVIDER_YOU, YOU_API_KEY_HEADER
from web_router.constants_you import (
    MSG_YOU_ANSWER_DOMAINS_EXCLUSIVE,
    YOU_ANSWER_MARKER,
    YOU_ANSWER_PARAM_NAMES,
    YOU_ANSWER_PATH,
    YOU_FRESHNESS_FIELD,
    YOU_RECENCY,
    YOU_RESEARCH_BASE_URL,
)
from web_router.providers._http import HttpAnswerProvider
from web_router.providers._utils import build_payload, mapped
from web_router.providers.answer import refuse_domain_pair
from web_router.types import Citation
from web_router.types_answer import AnswerRequest, AnswerResponse


class YouAnswerProvider(HttpAnswerProvider):
    """Answers a question through the You.com Answer API, ``POST /v1/answer`` on ``api.you.com``.

    The answer cites as ``[[1, 2]]``, numbering ``citations`` -- only the
    sources it cites -- from 1; each citation carries the first marker naming it
    in ``field``. A citation is ``{source, excerpts}``, so its title is joined
    from ``results.web``, the pages considered, by URL, and its snippet is the
    excerpts. There is no request id and no early stop: ``request_id`` is
    ``None`` and ``outcome`` always ``complete``.

    ``include_domains``, ``exclude_domains`` and ``country`` map to You.com's own
    fields, and ``recency`` to ``freshness``. The two domain lists cannot be sent
    together, so asking for both raises before the call. ``locale`` is refused:
    ``language`` showed no effect live.
    """

    name: ClassVar[str] = PROVIDER_YOU
    supported_parameters: ClassVar[frozenset[str]] = frozenset({*YOU_ANSWER_PARAM_NAMES, "recency"})

    base_url: ClassVar[str] = YOU_RESEARCH_BASE_URL
    answer_path: ClassVar[str] = YOU_ANSWER_PATH
    env_api_key: ClassVar[str] = ENV_YOU_API_KEY
    api_key_header: ClassVar[str] = YOU_API_KEY_HEADER

    def _build_payload(self, request: AnswerRequest) -> dict[str, Any]:
        refuse_domain_pair(self.name, request, MSG_YOU_ANSWER_DOMAINS_EXCLUSIVE)
        return build_payload(
            request,
            YOU_ANSWER_PARAM_NAMES,
            query=request.query,
            **{YOU_FRESHNESS_FIELD: mapped(YOU_RECENCY, request.recency)},
        )

    def _to_response(self, request: AnswerRequest, payload: dict[str, Any]) -> AnswerResponse:
        answer = payload.get("answer") or ""
        pages = {page.get("url"): page for page in (payload.get("results") or {}).get("web") or []}
        markers: dict[int, str] = {}
        for match in YOU_ANSWER_MARKER.finditer(answer):
            for number in match.group(1).split(","):
                markers.setdefault(int(number), match.group(0))
        return AnswerResponse(
            provider=self.name,
            query=request.query,
            answer=answer,
            citations=[
                self._to_citation(cited, pages.get(cited.get("source")) or {}, markers.get(position))
                for position, cited in enumerate(payload.get("citations") or [], start=1)
            ],
            raw=[payload],
        )

    @staticmethod
    def _to_citation(cited: dict[str, Any], page: dict[str, Any], marker: str | None) -> Citation:
        return Citation(
            url=cited.get("source") or "",
            title=page.get("title") or "",
            snippet="\n\n".join(cited.get("excerpts") or []),
            field=marker,
            extra={key: value for key, value in page.items() if key not in {"url", "title"}},
        )
