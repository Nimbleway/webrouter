"""``answer()`` on the routers, kept apart so ``client.py`` stops growing.

:class:`~web_router.WebRouter` and :class:`~web_router.AsyncWebRouter` take these as
mixins, so ``router.answer`` and ``router.get_answer_provider`` are where they always were.
"""

from __future__ import annotations

import threading
from typing import Any

from web_router._validation import refused_as_parameter
from web_router.providers.answer import AnswerProvider
from web_router.registry_answer import create_answer_provider, default_answer_provider
from web_router.routing import Budget
from web_router.routing.routed import AsyncRouted, SyncRouted
from web_router.types import Provider, provider_name
from web_router.types_answer import AnswerRequest, AnswerResponse, Recency


class _AnswerLookup:
    """What both routers share for ``answer()``: which backend serves it, and the request it is sent."""

    default_provider: str
    _config: dict[str, dict[str, Any]]
    _providers_lock: threading.Lock
    _answer_instances: dict[str, AnswerProvider]

    def _config_for(self, key: str) -> dict[str, Any]:
        raise NotImplementedError

    def get_answer_provider(self, name: str | Provider | AnswerProvider | None = None) -> AnswerProvider:
        """Return the named answer provider, constructing and caching it on first use.

        The answer counterpart of :meth:`~web_router.WebRouter.get_provider`, with
        the same ownership rule. With no name it uses the router's default when
        that serves answers -- ``auto``, the configured backends in ranked order,
        included -- else the one configured answer backend (a key in
        ``provider_config`` counts), and otherwise raises
        :class:`~web_router.ProviderNotFoundError`.
        """
        if isinstance(name, AnswerProvider):
            return name

        key = provider_name(name) if name is not None else default_answer_provider(self.default_provider, self._config)
        with self._providers_lock:
            if key not in self._answer_instances:
                self._answer_instances[key] = create_answer_provider(key, **self._config_for(key))
            return self._answer_instances[key]

    @staticmethod
    @refused_as_parameter(AnswerRequest)
    def _answer_request(query: str, provider_options: dict[str, Any] | None, **options: Any) -> AnswerRequest:
        return AnswerRequest(query=query, provider_options=provider_options or {}, **options)


class SyncAnswer(_AnswerLookup, SyncRouted):
    def answer(
        self,
        query: str,
        *,
        provider: str | Provider | AnswerProvider | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        country: str | None = None,
        locale: str | None = None,
        recency: Recency | None = None,
        provider_options: dict[str, Any] | None = None,
        budget: Budget | None = None,
    ) -> AnswerResponse:
        """Ask ``provider`` one question and return its written answer with the sources it rests on.

        One call, answered in seconds: the backend searches and writes the
        answer itself, and nothing here composes or edits it. Between
        :meth:`~web_router.WebRouter.search`, which returns the pages, and
        :meth:`~web_router.WebRouter.research`, a job of minutes. An option the
        backend does not map raises :class:`~web_router.UnsupportedParameterError`
        before anything is sent.

        With ``provider`` unset the router's default serves the call when it is
        an answer backend. On the usual default, ``auto``, that is the answer
        backends you hold a key for, in ranked order: one that fails or answers
        with nothing is passed over for the next, and each one tried bills.
        """
        request = self._answer_request(
            query,
            provider_options,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            country=country,
            locale=locale,
            recency=recency,
        )
        chain = self.get_answer_provider(provider)
        return self._routed(chain, request, budget, chain.answer)


class AsyncAnswer(_AnswerLookup, AsyncRouted):
    async def answer(
        self,
        query: str,
        *,
        provider: str | Provider | AnswerProvider | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        country: str | None = None,
        locale: str | None = None,
        recency: Recency | None = None,
        provider_options: dict[str, Any] | None = None,
        budget: Budget | None = None,
    ) -> AnswerResponse:
        """Ask ``provider`` one question and await its written answer, as on the sync router."""
        request = self._answer_request(
            query,
            provider_options,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            country=country,
            locale=locale,
            recency=recency,
        )
        chain = self.get_answer_provider(provider)
        return await self._arouted(chain, request, budget, chain.aanswer)
