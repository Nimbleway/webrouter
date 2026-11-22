"""A judge on any model LiteLLM reaches: ``--judge litellm:<model>``, so evaluation is not tied to one vendor.

``<model>`` is LiteLLM's own name for it -- ``gemini/gemini-3.1-flash-lite``,
``deepinfra/<org>/<model>``, ``bedrock/<id>``, ``ollama/<name>`` -- and LiteLLM
reads that provider's key from the environment under the provider's usual
name. The SDK comes with the ``evals-litellm`` extra.
"""

from __future__ import annotations

import os
from typing import Any, ClassVar

from web_router.constants import EVALS_JUDGE_TEMPERATURE, EVALS_JUDGE_TIMEOUT, MSG_EVALS_JUDGE_STOPPED
from web_router.constants_evals import (
    EVALS_LITELLM_ENDPOINT_SUFFIXES,
    EVALS_LITELLM_IMPORT_ENV,
    EVALS_LITELLM_JUDGE_NAME,
    EVALS_LITELLM_STOPPED_SHORT,
    EXTRA_EVALS_LITELLM,
    MSG_JUDGE_MISSING_KEY,
)
from web_router.evals.judge import Judge
from web_router.evals.labels import EndpointLabel
from web_router.exceptions import ConfigurationError, ProviderError, ProviderNotInstalledError


class LiteLLMJudge(EndpointLabel, Judge):
    """A judge on LiteLLM's ``completion()``, for a model neither built-in judge serves.

    It asks for ``temperature`` 0, as :class:`~web_router.evals.OpenAIJudge`
    does, and lets LiteLLM drop it for a model that takes none, so the model
    named in :attr:`label` is what fixes how it samples. ``base_url`` points it
    at another endpoint (LiteLLM's ``api_base``), and the label then names the
    host. A reply cut short or filtered raises rather than being graded.
    """

    name: ClassVar[str] = EVALS_LITELLM_JUDGE_NAME

    def __init__(
        self,
        model: str,
        *,
        base_url: str | None = None,
        temperature: float | None = EVALS_JUDGE_TEMPERATURE,
        timeout: float = EVALS_JUDGE_TIMEOUT,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self.timeout = timeout

    def check(self) -> None:
        """Raise now what the first call would raise: the SDK is missing, or the model's provider has no key."""
        sdk = self._sdk()
        missing = sdk.validate_environment(self.model, api_base=self.base_url).get("missing_keys") or []
        missing = [name for name in missing if not name.endswith(EVALS_LITELLM_ENDPOINT_SUFFIXES)]
        if missing:
            raise ConfigurationError(MSG_JUDGE_MISSING_KEY.format(judge=self.name, variable=missing[0]))

    def _sdk(self) -> Any:
        # On import LiteLLM reads the working directory's .env and fetches its price list from the web. The
        # library reads only the process environment and sends nothing it was not asked to, so both are off
        # unless the caller set them.
        for name, value in EVALS_LITELLM_IMPORT_ENV.items():
            os.environ.setdefault(name, value)
        try:
            import litellm
        except ImportError as exc:
            raise ProviderNotInstalledError(self.name, EXTRA_EVALS_LITELLM) from exc
        litellm.suppress_debug_info = True  # its help banner would print into the caller's output on every failure
        return litellm

    def _request(self, system: str, user: str) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(system, user),
            "timeout": self.timeout,
            "drop_params": True,
        }
        if self.temperature is not None:
            request["temperature"] = self.temperature
        if self.base_url:
            request["api_base"] = self.base_url
        return request

    def _complete(self, system: str, user: str) -> str:
        return self._text(self._sdk().completion(**self._request(system, user)))

    async def _acomplete(self, system: str, user: str) -> str:
        return self._text(await self._sdk().acompletion(**self._request(system, user)))

    def __repr__(self) -> str:
        return f"LiteLLMJudge(model={self.model!r})"

    def _text(self, response: Any) -> str:
        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) in EVALS_LITELLM_STOPPED_SHORT:
            raise ProviderError(self.name, MSG_EVALS_JUDGE_STOPPED.format(stop_reason=choice.finish_reason))
        return choice.message.content or ""
