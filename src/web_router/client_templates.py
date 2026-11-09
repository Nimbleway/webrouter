"""``templates()``, ``template()`` and ``run_template()`` on the routers, kept apart so ``client.py`` stops growing.

:class:`~web_router.WebRouter` and :class:`~web_router.AsyncWebRouter` take these as
mixins, so ``router.run_template`` and ``router.get_template_provider`` are where they always were.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from web_router.presets import PresetRegistry
from web_router.providers.base import TemplateProvider
from web_router.registry import create_template_provider
from web_router.types import (
    ExtractFormat,
    Provider,
    Template,
    TemplatePage,
    TemplateRequest,
    TemplateResponse,
    provider_name,
)
from web_router.types_presets import Preset


class _TemplateLookup:
    """What both routers share for templates: which backend serves them."""

    _providers_lock: threading.Lock
    _template_instances: dict[str, TemplateProvider]
    presets: PresetRegistry

    def _config_for(self, key: str) -> dict[str, Any]:
        raise NotImplementedError

    def get_template_provider(self, name: str | Provider | TemplateProvider) -> TemplateProvider:
        """Return the named template provider, constructing and caching it on first use.

        The template counterpart of :meth:`get_provider`, with the same
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


class SyncTemplates(_TemplateLookup):
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

    def run_template(
        self,
        template: str,
        inputs: Mapping[str, Any] | None = None,
        *,
        provider: str | Provider | TemplateProvider,
        format: ExtractFormat | None = None,
        provider_options: dict[str, Any] | None = None,
        preset: str | Preset | None = None,
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

        ``preset`` applies a saved configuration to this call: see :mod:`web_router.presets`.
        """
        plan = self.presets.resolve("templates", preset, format=format, provider_options=provider_options)
        options = plan.options
        request = TemplateRequest(
            template=template,
            inputs=dict(inputs or {}),
            format=options["format"],
            provider_options=options["provider_options"] or {},
        )
        with plan.explained():
            response = self.get_template_provider(provider).run(request)
        return plan.stamp(response)


class AsyncTemplates(_TemplateLookup):
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

    async def run_template(
        self,
        template: str,
        inputs: Mapping[str, Any] | None = None,
        *,
        provider: str | Provider | TemplateProvider,
        format: ExtractFormat | None = None,
        provider_options: dict[str, Any] | None = None,
        preset: str | Preset | None = None,
    ) -> TemplateResponse:
        """Run one of ``provider``'s templates on one page and return its record.

        As on the sync router: ``inputs`` are the template's own, and
        ``format`` asks for the page beside the record.

        ``preset`` applies a saved configuration to this call: see :mod:`web_router.presets`.
        """
        plan = self.presets.resolve("templates", preset, format=format, provider_options=provider_options)
        options = plan.options
        request = TemplateRequest(
            template=template,
            inputs=dict(inputs or {}),
            format=options["format"],
            provider_options=options["provider_options"] or {},
        )
        with plan.explained():
            response = await self.get_template_provider(provider).arun(request)
        return plan.stamp(response)
