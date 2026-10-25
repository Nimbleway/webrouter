"""The Nimble Extract Template provider, over the ``nimble-python`` SDK's ``extract.templates`` resource.

Kept apart from :mod:`web_router.providers.nimble`, which holds the search,
extract and research classes, so that module stops growing; it shares their
SDK clients and constructor through :class:`~web_router.providers.nimble._NimbleClients`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, ClassVar

from nimble_python import NotFoundError

from web_router.constants import (
    MSG_TEMPLATE_BAD_STATUS,
    MSG_TEMPLATE_NO_RECORD,
    MSG_TEMPLATE_PAGE_FAILED,
    MSG_TEMPLATE_PARSING_FAILED,
    NIMBLE_EXTRACT_FORMATS,
    NIMBLE_TEMPLATE_ERROR_FIELD,
    NIMBLE_TEMPLATE_ERROR_SHAPE,
    NIMBLE_TEMPLATE_FAILED_STATUS_FLOOR,
    NIMBLE_TEMPLATE_PARSING_ERROR_STATUS,
    NIMBLE_TEMPLATE_PARSING_SUCCESS_STATUS,
    NIMBLE_TEMPLATE_RECORD_FIELD,
    NIMBLE_TEMPLATE_RECORD_SHAPE,
    NIMBLE_TEMPLATE_STATUS_FIELD,
    NIMBLE_TEMPLATE_SUCCESS_STATUS,
    PROVIDER_NIMBLE,
)
from web_router.exceptions import ProviderError, TemplateNotFoundError
from web_router.providers._utils import build_payload, compact, model_to_dict
from web_router.providers.base import TemplateProvider
from web_router.providers.nimble import _as_int, _NimbleClients
from web_router.types import (
    TEMPLATE_PARAMETERS,
    ExtractedDocument,
    ExtractFormat,
    Template,
    TemplatePage,
    TemplateRequest,
    TemplateResponse,
)


class NimbleTemplateProvider(_NimbleClients, TemplateProvider):
    """Lists, describes and runs Nimble Extract Templates.

    A template run is the Extract API's realtime endpoint with a template
    named instead of a URL: the template resolves its own inputs to the page,
    fetches it and parses it into a record. The response is the same task
    envelope an extract answers with, so a page the backend could not fetch
    is told the same way, by ``status``, and the record arrives under
    ``data.parsing``.
    """

    name: ClassVar[str] = PROVIDER_NIMBLE
    supported_parameters: ClassVar[frozenset[str]] = TEMPLATE_PARAMETERS
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(NIMBLE_EXTRACT_FORMATS)

    def _run(self, request: TemplateRequest) -> TemplateResponse:
        with _unknown_template_named(self.name, request.template):
            raw = self._sync_client.extract.templates.run(**self._build_payload(request))
        return self._to_response(request, model_to_dict(raw))

    async def _arun(self, request: TemplateRequest) -> TemplateResponse:
        with _unknown_template_named(self.name, request.template):
            raw = await self._async_client.extract.templates.run(**self._build_payload(request))
        return self._to_response(request, model_to_dict(raw))

    def _list(self, *, limit: int | None, offset: int | None) -> TemplatePage:
        raw = self._sync_client.extract.templates.list(**compact({"limit": limit, "offset": offset}))
        return self._to_page(model_to_dict(raw))

    async def _alist(self, *, limit: int | None, offset: int | None) -> TemplatePage:
        raw = await self._async_client.extract.templates.list(**compact({"limit": limit, "offset": offset}))
        return self._to_page(model_to_dict(raw))

    def _get(self, name: str) -> Template:
        with _unknown_template_named(self.name, name):
            raw = self._sync_client.extract.templates.get(name)
        return self._to_template(model_to_dict(raw))

    async def _aget(self, name: str) -> Template:
        with _unknown_template_named(self.name, name):
            raw = await self._async_client.extract.templates.get(name)
        return self._to_template(model_to_dict(raw))

    def _build_payload(self, request: TemplateRequest) -> dict[str, Any]:
        """Translate a normalized request into Nimble ``extract.templates.run()`` keyword arguments.

        ``formats`` is sent only when a format was asked for: Nimble returns no
        page content without it, which is what an unset format means.
        """
        return build_payload(
            request,
            template=request.template,
            params=dict(request.inputs),
            formats=[NIMBLE_EXTRACT_FORMATS[request.format]] if request.format else None,
        )

    def _to_response(self, request: TemplateRequest, payload: dict[str, Any]) -> TemplateResponse:
        status = payload.get("status")
        data = payload.get("data") or {}

        # As for an extract: a block or a fatal fetch error is a 200 whose
        # `status` says so, and a record cannot have been parsed from nothing.
        if status != NIMBLE_TEMPLATE_SUCCESS_STATUS:
            detail = MSG_TEMPLATE_BAD_STATUS.format(template=request.template, status=status)
            raise ProviderError(self.name, "; ".join([detail, *(payload.get("warnings") or [])]))

        # The envelope says `success` whenever the fetch completed, a 404 page
        # included, and the template then parses the error page. What it
        # parsed is not the record the template promises.
        status_code = _as_int(payload.get("status_code"))
        if status_code is not None and status_code >= NIMBLE_TEMPLATE_FAILED_STATUS_FLOOR:
            detail = MSG_TEMPLATE_PAGE_FAILED.format(template=request.template, status=status_code)
            raise ProviderError(self.name, "; ".join([detail, *(payload.get("warnings") or [])]))

        record = self._record(request, data.get("parsing"))
        document = self._document(request, payload, data)

        return TemplateResponse(
            provider=self.name,
            template=request.template,
            inputs=dict(request.inputs),
            data=record,
            url=payload.get("url"),
            document=document,
            status_code=_as_int(payload.get("status_code")),
            request_id=payload.get("task_id"),
            # Read rather than popped, so `raw` keeps everything; the page
            # body and the record are left out because each has its own field.
            extra=compact(
                {
                    "metadata": payload.get("metadata"),
                    "pagination": payload.get("pagination"),
                    **{k: v for k, v in data.items() if k not in ("parsing", *NIMBLE_EXTRACT_FORMATS.values())},
                }
            ),
            raw=payload,
        )

    def _record(self, request: TemplateRequest, parsing: Any) -> dict[str, Any]:
        """The record under ``data.parsing``, in whichever of its three shapes Nimble sent it.

        See ``NIMBLE_TEMPLATE_PARSING_SUCCESS_STATUS``: the record may arrive
        bare, or wrapped beside a status that says it parsed, or replaced by
        one that says it did not.

        Raises:
            ProviderError: when the template reports that it failed to parse
                the page, or the page came back with no record at all.
        """
        if not isinstance(parsing, dict) or not parsing:
            raise ProviderError(self.name, MSG_TEMPLATE_NO_RECORD.format(template=request.template))
        # A wrapper is exactly the SDK's two fields and nothing else: a record
        # whose own schema names `status` and `entities` beside other fields is
        # still the record, and unwrapping it would lose the others.
        reported = parsing.get(NIMBLE_TEMPLATE_STATUS_FIELD)
        if set(parsing) == NIMBLE_TEMPLATE_ERROR_SHAPE and reported == NIMBLE_TEMPLATE_PARSING_ERROR_STATUS:
            error = parsing[NIMBLE_TEMPLATE_ERROR_FIELD]
            raise ProviderError(self.name, MSG_TEMPLATE_PARSING_FAILED.format(template=request.template, error=error))
        if (
            set(parsing) == NIMBLE_TEMPLATE_RECORD_SHAPE
            and reported == NIMBLE_TEMPLATE_PARSING_SUCCESS_STATUS
            and isinstance(parsing[NIMBLE_TEMPLATE_RECORD_FIELD], dict)
        ):
            return dict(parsing[NIMBLE_TEMPLATE_RECORD_FIELD])
        return dict(parsing)

    def _document(
        self, request: TemplateRequest, payload: dict[str, Any], data: dict[str, Any]
    ) -> ExtractedDocument | None:
        """The page beside the record, only when a format asked for it."""
        if request.format is None:
            return None
        return ExtractedDocument(
            url=payload.get("url") or "",
            content=data.get(NIMBLE_EXTRACT_FORMATS[request.format]) or "",
            format=request.format,
            status_code=_as_int(payload.get("status_code")),
        )

    def _to_page(self, payload: dict[str, Any]) -> TemplatePage:
        return TemplatePage(
            provider=self.name,
            templates=[self._to_template(item) for item in payload.get("items") or []],
            total=_as_int(payload.get("total")),
            offset=_as_int(payload.get("offset")),
            limit=_as_int(payload.get("limit")),
            raw=payload,
        )

    def _to_template(self, item: dict[str, Any]) -> Template:
        """One template as Nimble describes it: the published version carries everything a run needs."""
        version = item.get("published_version") or {}
        metadata = version.get("metadata") or {}
        return Template(
            provider=self.name,
            name=item.get("name") or "",
            description=metadata.get("description") or "",
            domain=metadata.get("domain"),
            entity_type=metadata.get("entity_type"),
            input_schema=version.get("input_schema"),
            output_schema=version.get("output_schema"),
            version=_as_int(version.get("version_number")),
            published=bool(version),
            created_at=item.get("created_at"),
            updated_at=item.get("updated_at"),
            extra=compact(
                {
                    **{k: v for k, v in item.items() if k not in _TEMPLATE_MAPPED},
                    **{k: v for k, v in metadata.items() if k not in _TEMPLATE_METADATA_MAPPED},
                    "samples": version.get("samples"),
                }
            ),
            raw=item,
        )


@contextmanager
def _unknown_template_named(provider: str, template: str) -> Iterator[None]:
    """Turn Nimble's 404 for a template name into :class:`TemplateNotFoundError`, on a run and on a describe alike."""
    try:
        yield
    except NotFoundError as exc:
        raise TemplateNotFoundError(provider, template, original=exc) from exc


_TEMPLATE_MAPPED = frozenset({"name", "created_at", "updated_at", "published_version"})
_TEMPLATE_METADATA_MAPPED = frozenset({"description", "domain", "entity_type"})
