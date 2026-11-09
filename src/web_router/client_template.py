"""``templates()``, ``template()`` and ``run_template()`` on the routers, kept apart so ``client.py`` stops growing.

:class:`~web_router.WebRouter` and :class:`~web_router.AsyncWebRouter` take these as
mixins, as they take ``answer()`` from :mod:`web_router.client_answer`, so the
methods are where they always were.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from web_router.client_presets import with_preset
from web_router.client_requests import build_template_request
from web_router.providers.base import TemplateProvider
from web_router.registry import create_template_provider
from web_router.types import (
    ExtractFormat,
    Provider,
    Template,
    TemplatePage,
    TemplateResponse,
    provider_name,
)


class _TemplateLookup:
    """What both routers share for templates: the backend that holds them."""

    _providers_lock: threading.Lock
    _template_instances: dict[str, TemplateProvider]

    def _config_for(self, key: str) -> dict[str, Any]:
        raise NotImplementedError

    def get_template_provider(self, name: str | Provider | TemplateProvider) -> TemplateProvider:
        """Return the named template provider, constructing and caching it on first use.

        The template counterpart of :meth:`~web_router.WebRouter.get_provider`, with the same
        ownership rule -- but no default: a template's name belongs to one
        vendor, so the caller always says which.
        """
        if isinstance(name, TemplateProvider):
            return name

        key = provider_name(name)
        with self._providers_lock:
            if key not in self._template_instances:
                self._template_instances[key] = create_template_provider(key, **self._config_for(key))
            return self._template_instances[key]


class SyncTemplate(_TemplateLookup):
    def templates(
        self,
        *,
        provider: str | Provider | TemplateProvider,
        limit: int | None = None,
        offset: int | None = None,
    ) -> TemplatePage:
        """List one page of the templates ``provider`` holds.

        A template is a vendor's saved, site-specific extraction: run on one
        page of the site it was built for, it returns a record with fixed
        fields. Names, inputs and fields are the vendor's own, which is why
        ``provider`` has no default. The page says where in the catalogue it
        sits and, when the backend reports it, how many templates there are
        in all; ``limit`` and ``offset`` left unset leave the paging to the
        backend.
        """
        return self.get_template_provider(provider).templates(limit=limit, offset=offset)

    def template(
        self,
        name: str,
        *,
        provider: str | Provider | TemplateProvider,
    ) -> Template:
        """Describe one template: the inputs a run takes and the record it returns, as JSON Schema."""
        return self.get_template_provider(provider).template(name)

    @with_preset("run_template")
    def run_template(
        self,
        template: str,
        inputs: Mapping[str, Any] | None = None,
        *,
        provider: str | Provider | TemplateProvider,
        format: ExtractFormat | None = None,
        provider_options: dict[str, Any] | None = None,
        preset: str | None = None,
    ) -> TemplateResponse:
        """Run one of ``provider``'s templates on one page and return its record.

        ``inputs`` are the template's own, named by its
        :attr:`~web_router.Template.input_schema` -- a URL for some
        templates, an id, a slug or a search term for others. ``format`` asks
        for the page beside the record; unset, the record comes back alone.
        A run the backend could not complete raises rather than coming back
        with an empty record, and an unknown name raises
        :class:`~web_router.TemplateNotFoundError`. One call is one page and
        one set of inputs: a backend whose templates run as jobs over many
        inputs would answer through its synchronous endpoint, under that
        endpoint's own time cap.
        """
        request = build_template_request(template, inputs, format=format, provider_options=provider_options)
        return self.get_template_provider(provider).run(request)


class AsyncTemplate(_TemplateLookup):
    async def templates(
        self,
        *,
        provider: str | Provider | TemplateProvider,
        limit: int | None = None,
        offset: int | None = None,
    ) -> TemplatePage:
        """List one page of the templates ``provider`` holds."""
        return await self.get_template_provider(provider).atemplates(limit=limit, offset=offset)

    async def template(
        self,
        name: str,
        *,
        provider: str | Provider | TemplateProvider,
    ) -> Template:
        """Describe one template: the inputs a run takes and the record it returns, as JSON Schema."""
        return await self.get_template_provider(provider).atemplate(name)

    @with_preset("run_template")
    async def run_template(
        self,
        template: str,
        inputs: Mapping[str, Any] | None = None,
        *,
        provider: str | Provider | TemplateProvider,
        format: ExtractFormat | None = None,
        provider_options: dict[str, Any] | None = None,
        preset: str | None = None,
    ) -> TemplateResponse:
        """Run one of ``provider``'s templates on one page and return its record.

        As on the sync router: ``inputs`` are the template's own, and
        ``format`` asks for the page beside the record.
        """
        request = build_template_request(template, inputs, format=format, provider_options=provider_options)
        return await self.get_template_provider(provider).arun(request)
