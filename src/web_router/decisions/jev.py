"""TypeSafe's Jev, asked typed questions over the System One API."""

from __future__ import annotations

from typing import Any

import httpx

from web_router.constants import (
    ENV_TYPESAFE_API_KEY,
    FOLLOW_REDIRECTS,
    HTTP_ERROR_BODY_LIMIT,
    JEV_MODEL,
    MSG_HTTP_ERROR,
    MSG_ROUTE_BAD_ANSWER,
    ROUTE_DECIDER_CONNECT_TIMEOUT,
    ROUTE_DECIDER_JEV,
    ROUTE_DECIDER_TIMEOUT,
    TYPESAFE_BASE_URL,
    TYPESAFE_DECISION_PATH,
)
from web_router.decisions.route import Answer
from web_router.exceptions import ProviderError
from web_router.providers._utils import resolve_api_key

DECIDER_NAME = ROUTE_DECIDER_JEV


class JevDecider:
    """Asks Jev a set of choice questions about one state in a single call.

    Any server speaking the System One shape works through ``base_url``: a
    gateway's TypeSafe-compatible route, or a local open model.
    """

    name = DECIDER_NAME

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = TYPESAFE_BASE_URL,
        model: str = JEV_MODEL,
        timeout: float = ROUTE_DECIDER_TIMEOUT,
    ) -> None:
        """
        Raises:
            ConfigurationError: if no key is passed and ``TYPESAFE_API_KEY`` is unset,
                here rather than on the first query.
        """
        key = resolve_api_key(api_key, env_var=ENV_TYPESAFE_API_KEY, provider=DECIDER_NAME)
        self.model = model
        # Built here: an httpx.Client makes no network call until used, and one shared client is thread-safe.
        self._client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout, connect=min(timeout, ROUTE_DECIDER_CONNECT_TIMEOUT)),
            headers={"Accept": "application/json", "Authorization": f"Bearer {key}"},
            follow_redirects=FOLLOW_REDIRECTS,
        )

    def decide(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, Answer]:
        """Answer every question about ``state``, one request.

        Raises:
            ProviderError: on an HTTP error, a timeout, or an answer that cannot be read.
        """
        body = {"state": state, "model": self.model, "questions": questions}
        try:
            response = self._client.post(TYPESAFE_DECISION_PATH, json=body)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            detail = MSG_HTTP_ERROR.format(
                status=exc.response.status_code, body=exc.response.text[:HTTP_ERROR_BODY_LIMIT]
            )
            raise ProviderError(self.name, detail, original=exc) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(self.name, str(exc) or type(exc).__name__, original=exc) from exc
        try:
            return {
                name: Answer(
                    choice=str(answer["choice"]),
                    confidence=float(answer["confidence"]),
                    probabilities={k: float(v) for k, v in (answer.get("probabilities") or {}).items()},
                )
                for name, answer in payload["answers"].items()
            }
        except (AttributeError, KeyError, TypeError, ValueError) as exc:  # any reply of the wrong shape
            raise ProviderError(self.name, MSG_ROUTE_BAD_ANSWER.format(detail=exc), original=exc) from exc

    def close(self) -> None:
        self._client.close()
