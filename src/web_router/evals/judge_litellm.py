"""A judge on any model LiteLLM reaches: ``--judge litellm:<model>``, so evaluation is not tied to one vendor.

``<model>`` is LiteLLM's own name for it -- ``openrouter/z-ai/glm-5``,
``gemini/gemini-3-pro``, ``bedrock/<id>``, ``ollama/<name>`` -- and LiteLLM
reads that provider's key from the environment under the provider's usual
name. The SDK comes with the ``evals-litellm`` extra.
"""

from __future__ import annotations

import os
from typing import Any, ClassVar

from web_router.constants import EVALS_JUDGE_TEMPERATURE, EVALS_JUDGE_TIMEOUT
from web_router.constants_evals import EVALS_LITELLM_IMPORT_ENV, EVALS_LITELLM_JUDGE_NAME, EXTRA_EVALS_LITELLM
from web_router.evals.judge import Judge
from web_router.evals.labels import judge_label
from web_router.exceptions import ConfigurationError, ProviderNotInstalledError


class LiteLLMJudge(Judge):
    """A judge on LiteLLM's ``completion()``, for a model neither built-in judge serves.

    It asks for ``temperature`` 0, as :class:`~web_router.evals.OpenAIJudge`
    does, and lets LiteLLM drop it for a model that takes none, so the model
    named in :attr:`label` is what fixes how it samples. ``api_base`` points it
    at another endpoint, and the label then names the host.
    """

    name: ClassVar[str] = EVALS_LITELLM_JUDGE_NAME

    def __init__(
        self,
        model: str,
        *,
        api_base: str | None = None,
        temperature: float | None = EVALS_JUDGE_TEMPERATURE,
        timeout: float = EVALS_JUDGE_TIMEOUT,
    ) -> None:
        self.model = model
        self.api_base = api_base
        self.temperature = temperature
        self.timeout = timeout

    @property
    def label(self) -> str:
        return judge_label(self.name, self.model, self.api_base)

    def check(self) -> None:
        """Raise now what the first call would raise: the SDK is missing, or the model's provider has no key."""
        missing = self._sdk().validate_environment(self.model, api_base=self.api_base).get("missing_keys") or []
        if missing:
            raise ConfigurationError.missing_api_key(self.name, missing[0])

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
        return litellm

    def _request(self, system: str, user: str) -> dict[str, Any]:
        messages = [{"role": "system", "content": system}] if system else []
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [*messages, {"role": "user", "content": user}],
            "timeout": self.timeout,
            "drop_params": True,
        }
        if self.temperature is not None:
            request["temperature"] = self.temperature
        if self.api_base:
            request["api_base"] = self.api_base
        return request

    def _complete(self, system: str, user: str) -> str:
        return _text(self._sdk().completion(**self._request(system, user)))

    async def _acomplete(self, system: str, user: str) -> str:
        return _text(await self._sdk().acompletion(**self._request(system, user)))

    def __repr__(self) -> str:
        return f"LiteLLMJudge(model={self.model!r})"


def _text(response: Any) -> str:
    return response.choices[0].message.content or ""
