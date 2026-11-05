"""The judge: one model that turns a provider's results into an answer, then grades that answer.

Both steps go through the same model so the only thing that varies between
providers is the retrieval feeding it -- which is what makes the accuracy
column one question asked of every backend. The grader prompt is the SimpleQA
prompt from openai/simple-evals (``simpleqa_eval.py``, MIT, (c) 2024 OpenAI),
vendored byte-for-byte under ``prompts/`` so a reader can diff it against
upstream; the synthesis prompt is ours, in ``constants.py``.

:class:`OpenAIJudge` is the default and :class:`AnthropicJudge` the other
built-in one. Any :class:`Judge` subclass may stand in for them -- another
vendor, a local model, a fake in a test.
"""

from __future__ import annotations

import os
import re
import threading
from abc import ABC, abstractmethod
from functools import lru_cache
from importlib import resources
from typing import Any, ClassVar, cast

from web_router.constants import (
    ENV_ANTHROPIC_API_KEY,
    ENV_OPENAI_API_KEY,
    EVALS_ANTHROPIC_COMPLETE_STOP_REASONS,
    EVALS_ANTHROPIC_JUDGE_MAX_TOKENS,
    EVALS_ANTHROPIC_JUDGE_MODEL,
    EVALS_ANTHROPIC_JUDGE_NAME,
    EVALS_FIXED_TEMPERATURE_MODELS,
    EVALS_GRADE_BY_LETTER,
    EVALS_GRADE_PATTERN,
    EVALS_GRADER_PROMPT_RESOURCE,
    EVALS_JUDGE_MODEL,
    EVALS_JUDGE_NAME,
    EVALS_JUDGE_TEMPERATURE,
    EVALS_JUDGE_TIMEOUT,
    EVALS_RESULT_CHARS,
    EVALS_RESULT_FORMAT,
    EVALS_SYNTHESIS_PROMPT,
    EVALS_SYNTHESIS_USER,
    EXTRA_EVALS,
    MSG_EVALS_JUDGE_STOPPED,
)
from web_router.evals.types import Grade
from web_router.exceptions import ConfigurationError, ProviderError, ProviderNotInstalledError
from web_router.integrations._render import cut_to_budget
from web_router.types import SearchResult


@lru_cache(maxsize=1)
def grader_prompt() -> str:
    """The vendored SimpleQA grader template, with ``{question}``, ``{target}`` and ``{predicted_answer}`` slots."""
    text = resources.files(__package__).joinpath(EVALS_GRADER_PROMPT_RESOURCE).read_text(encoding="utf-8")
    return text.removesuffix("\n")


def format_results(results: list[SearchResult]) -> str:
    """The results as the synthesizer sees them, in the backend's own rank order."""
    rows = []
    for result in results:
        # The body, as the integrations hand it to an agent: a description is
        # the vendor's snippet for some backends and our cut of the page head
        # for others, so reading it ranked backends on where the cut fell.
        text = cut_to_budget(result.content or result.description, EVALS_RESULT_CHARS)
        rows.append(EVALS_RESULT_FORMAT.format(title=result.title, url=result.url, text=text))
    return "\n\n".join(rows)


def parse_grade(reply: str) -> Grade:
    """The grade a reply names; a reply naming none is ``not_attempted``, as upstream reads it."""
    match = re.search(EVALS_GRADE_PATTERN, reply or "")
    letter = match.group(1) if match else "C"
    return cast(Grade, EVALS_GRADE_BY_LETTER[letter])


class Judge(ABC):
    """Synthesizes an answer from search results and grades an answer against a gold target.

    Implement the two ``_complete`` methods -- one prompt in, the model's text
    out -- and set :attr:`name` to what the report should record.
    """

    name: ClassVar[str] = "judge"

    @property
    def label(self) -> str:
        """What the report records under ``judge``: the name, and the model where there is one."""
        return self.name

    def check(self) -> None:  # noqa: B027
        """Raise now what the first call would raise; a judge with nothing to check does nothing.

        An evaluation calls this before its first search, so a judge that
        cannot run costs nothing rather than failing every row after the
        searches were paid for.
        """

    @abstractmethod
    def _complete(self, system: str, user: str) -> str: ...

    @abstractmethod
    async def _acomplete(self, system: str, user: str) -> str: ...

    def synthesize(self, question: str, results: list[SearchResult]) -> str:
        """One answer to ``question`` from ``results`` and nothing else."""
        return self._complete(
            EVALS_SYNTHESIS_PROMPT, EVALS_SYNTHESIS_USER.format(query=question, results=format_results(results))
        )

    async def asynthesize(self, question: str, results: list[SearchResult]) -> str:
        return await self._acomplete(
            EVALS_SYNTHESIS_PROMPT, EVALS_SYNTHESIS_USER.format(query=question, results=format_results(results))
        )

    def grade(self, question: str, target: str, predicted: str) -> Grade:
        """Whether ``predicted`` contains ``target``, contradicts it, or does neither."""
        return parse_grade(
            self._complete("", grader_prompt().format(question=question, target=target, predicted_answer=predicted))
        )

    async def agrade(self, question: str, target: str, predicted: str) -> Grade:
        return parse_grade(
            await self._acomplete(
                "", grader_prompt().format(question=question, target=target, predicted_answer=predicted)
            )
        )


class OpenAIJudge(Judge):
    """The built-in judge, on OpenAI's chat completions -- ``gpt-4o`` by default.

    The ``openai`` SDK comes with the ``evals`` extra and is imported on first
    use, so a report can be built and read without it. ``base_url`` points it
    at any endpoint that speaks the same API.
    """

    name: ClassVar[str] = EVALS_JUDGE_NAME

    def __init__(
        self,
        model: str = EVALS_JUDGE_MODEL,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float | None = EVALS_JUDGE_TEMPERATURE,
        timeout: float = EVALS_JUDGE_TIMEOUT,
    ) -> None:
        """
        Args:
            temperature: Sent as given; ``None`` leaves the model's own. Left
                out regardless for a model that rejects any but its default.
        """
        self.model = model
        self.api_key = api_key if api_key is not None else os.environ.get(ENV_OPENAI_API_KEY)
        self.base_url = base_url
        self.temperature = None if model.startswith(EVALS_FIXED_TEMPERATURE_MODELS) else temperature
        self.timeout = timeout
        # Built on first use and separately, as the HTTP providers are: a sync
        # close cannot shut an async client, and building both would leak one.
        # Under a lock: sync evaluation shares one judge across worker threads,
        # and racing first calls would each build a client close() never sees.
        self._client: Any = None
        self._async_client: Any = None
        self._client_lock = threading.Lock()

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}"

    def check(self) -> None:
        """Raise now what the first call would raise: the SDK is missing, or there is no key."""
        self._sdk()

    def _sdk(self) -> Any:
        try:
            import openai
        except ImportError as exc:
            raise ProviderNotInstalledError(self.name, EXTRA_EVALS) from exc
        if not self.api_key:
            raise ConfigurationError.missing_api_key(self.name, ENV_OPENAI_API_KEY)
        return openai

    def _request(self, system: str, user: str) -> dict[str, Any]:
        messages = (
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
            if system
            else [{"role": "user", "content": user}]
        )
        request: dict[str, Any] = {"model": self.model, "messages": messages}
        if self.temperature is not None:
            request["temperature"] = self.temperature
        return request

    def _complete(self, system: str, user: str) -> str:
        with self._client_lock:
            if self._client is None:
                openai = self._sdk()
                self._client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)
            client = self._client
        response = client.chat.completions.create(**self._request(system, user))
        return str(response.choices[0].message.content or "")

    async def _acreate(self, system: str, user: str) -> Any:
        """The raw chat completion, for a caller that needs more of it than the text, such as its token usage."""
        # The same lock, held across no await: loops on several threads may share one judge.
        with self._client_lock:
            if self._async_client is None:
                openai = self._sdk()
                self._async_client = openai.AsyncOpenAI(
                    api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
                )
            client = self._async_client
        return await client.chat.completions.create(**self._request(system, user))

    async def _acomplete(self, system: str, user: str) -> str:
        response = await self._acreate(system, user)
        return str(response.choices[0].message.content or "")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    async def aclose(self) -> None:
        if self._async_client is not None:
            await self._async_client.close()
            self._async_client = None

    def __repr__(self) -> str:
        return f"<OpenAIJudge model={self.model!r}>"


class AnthropicJudge(Judge):
    """A judge on Anthropic's Messages API -- ``claude-sonnet-5-5`` by default.

    For when the default judge would be grading its own model family: a
    system built on GPT is better graded from outside it. The
    ``anthropic`` SDK comes with the ``evals`` extra and is imported on first
    use, as :class:`OpenAIJudge`'s is. ``base_url`` points it at any endpoint
    that speaks the same API.

    It sends no temperature: the SDK no longer takes one, and current Claude
    models refuse any but their own -- ``claude-sonnet-5-5`` answers
    ``temperature=0`` with a 400 -- so the model named in :attr:`label` is
    what fixes how it samples.
    """

    name: ClassVar[str] = EVALS_ANTHROPIC_JUDGE_NAME

    def __init__(
        self,
        model: str = EVALS_ANTHROPIC_JUDGE_MODEL,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int = EVALS_ANTHROPIC_JUDGE_MAX_TOKENS,
        timeout: float = EVALS_JUDGE_TIMEOUT,
    ) -> None:
        """
        Args:
            max_tokens: The most one reply may run to, thinking included. A
                reply that stops there, or on a refusal, raises instead of
                being graded.
        """
        self.model = model
        self.api_key = api_key if api_key is not None else os.environ.get(ENV_ANTHROPIC_API_KEY)
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.timeout = timeout
        # Lazily, separately and under a lock, for the reasons OpenAIJudge gives.
        self._client: Any = None
        self._async_client: Any = None
        self._client_lock = threading.Lock()

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}"

    def check(self) -> None:
        """Raise now what the first call would raise: the SDK is missing, or there is no key."""
        self._sdk()

    def _sdk(self) -> Any:
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderNotInstalledError(self.name, EXTRA_EVALS) from exc
        if not self.api_key:
            raise ConfigurationError.missing_api_key(self.name, ENV_ANTHROPIC_API_KEY)
        return anthropic

    def _request(self, system: str, user: str) -> dict[str, Any]:
        # No fallback model: a reply another model wrote would be filed under this one's label.
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": user}],
        }
        if system:
            request["system"] = system
        return request

    def _text(self, response: Any) -> str:
        if response.stop_reason not in EVALS_ANTHROPIC_COMPLETE_STOP_REASONS:
            raise ProviderError(self.name, MSG_EVALS_JUDGE_STOPPED.format(stop_reason=response.stop_reason))
        return "".join(block.text for block in response.content if block.type == "text")

    def _complete(self, system: str, user: str) -> str:
        with self._client_lock:
            if self._client is None:
                anthropic = self._sdk()
                self._client = anthropic.Anthropic(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)
            client = self._client
        return self._text(client.messages.create(**self._request(system, user)))

    async def _acomplete(self, system: str, user: str) -> str:
        with self._client_lock:
            if self._async_client is None:
                anthropic = self._sdk()
                self._async_client = anthropic.AsyncAnthropic(
                    api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
                )
            client = self._async_client
        return self._text(await client.messages.create(**self._request(system, user)))

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    async def aclose(self) -> None:
        if self._async_client is not None:
            await self._async_client.close()
            self._async_client = None

    def __repr__(self) -> str:
        return f"<AnthropicJudge model={self.model!r}>"
