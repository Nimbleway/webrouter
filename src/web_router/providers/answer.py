"""The answer method's base class, re-exported from :mod:`web_router.providers.base`.

Kept apart so ``base.py`` stops growing; import it from ``web_router.providers.base`` as before.
"""

from __future__ import annotations

import abc

from web_router.constants import MSG_ANSWER_FAILED
from web_router.providers.base import _BaseProvider
from web_router.types_answer import AnswerRequest, AnswerResponse

__all__ = ["AnswerProvider"]


class AnswerProvider(_BaseProvider):
    """Base class for an answer backend: one question in, a written answer and its sources out.

    One request answers in one call, so unlike research there is no job: set
    :attr:`name` and :attr:`supported_parameters`, then implement
    :meth:`_answer` and :meth:`_aanswer`.
    """

    # -- What subclasses implement -------------------------------------------

    @abc.abstractmethod
    def _answer(self, request: AnswerRequest) -> AnswerResponse:
        """Ask the backend. The request is already validated; errors are wrapped for you."""

    @abc.abstractmethod
    async def _aanswer(self, request: AnswerRequest) -> AnswerResponse:
        """Async counterpart of :meth:`_answer`."""

    # -- What callers use ----------------------------------------------------

    def answer(self, request: AnswerRequest) -> AnswerResponse:
        """Validate the request, ask the backend, and wrap any backend failure."""
        self.validate(request)
        return self._wrapped(lambda: self._answer(request), MSG_ANSWER_FAILED)

    async def aanswer(self, request: AnswerRequest) -> AnswerResponse:
        """Async counterpart of :meth:`answer`."""
        self.validate(request)
        return await self._awrapped(self._aanswer(request), MSG_ANSWER_FAILED)

    def validate(self, request: AnswerRequest) -> None:
        """Raise :class:`UnsupportedParameterError` for any option this backend does not map."""
        self._reject_unmapped(request)
