"""The answer method's base class, re-exported from :mod:`web_router.providers.base`.

Kept apart so ``base.py`` stops growing; import it from ``web_router.providers.base`` as before.
"""

from __future__ import annotations

import abc

from web_router.constants import ANSWER_MARKER, MSG_ANSWER_FAILED
from web_router.exceptions import UnsupportedParameterError
from web_router.providers.base import _BaseProvider
from web_router.types_answer import AnswerRequest, AnswerResponse

__all__ = ["AnswerProvider", "cited_marker", "refuse_domain_pair"]

_DOMAIN_LISTS = frozenset({"include_domains", "exclude_domains"})


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


def refuse_domain_pair(provider: str, request: AnswerRequest, detail: str) -> None:
    """Raise :class:`UnsupportedParameterError` when ``request`` sets both domain lists.

    For a backend that takes one list or the other, never both; ``detail`` says so in its words.
    """
    if all(getattr(request, name) for name in _DOMAIN_LISTS):
        raise UnsupportedParameterError(provider, set(_DOMAIN_LISTS), detail=detail)


def cited_marker(answer: str, number: object) -> str | None:
    """The ``[number]`` marker when ``answer`` cites it, else ``None`` -- a source's ``Citation.field``."""
    marker = ANSWER_MARKER.format(number=number)
    return marker if number is not None and marker in answer else None
