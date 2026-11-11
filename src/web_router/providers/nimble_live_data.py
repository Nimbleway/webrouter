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
    NIMBLE_EXTRACT_FORMATS,
    PROVIDER_NIMBLE,
)
from web_router.exceptions_live_data import LiveDataSourceNotFoundError
from web_router.providers._utils import build_payload, compact, model_to_dict
from web_router.providers.base import LiveDataProvider
from web_router.providers.nimble import _NimbleClients
from web_router.providers.nimble_live_data_map import to_page, to_response, to_source
from web_router.types import (
    ExtractFormat,
)
from web_router.types_live_data import (
    LIVE_DATA_PARAMETERS,
    LiveDataRequest,
    LiveDataResponse,
    LiveDataSource,
    LiveDataSourcePage,
)


class NimbleLiveDataProvider(_NimbleClients, LiveDataProvider):
    """Lists, describes and runs Nimble Extract Templates.

    A template run is the Extract API's realtime endpoint with a template
    named instead of a URL: the template resolves its own inputs to the page,
    fetches it and parses it into a record. The response is the same task
    envelope an extract answers with, so a page the backend could not fetch
    is told the same way, by ``status``, and the record arrives under
    ``data.parsing``.
    """

    name: ClassVar[str] = PROVIDER_NIMBLE
    supported_parameters: ClassVar[frozenset[str]] = LIVE_DATA_PARAMETERS
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(NIMBLE_EXTRACT_FORMATS)

    def _run(self, request: LiveDataRequest) -> LiveDataResponse:
        with _unknown_live_data_source_named(self.name, request.source):
            raw = self._within_deadline(self._sync_client).extract.templates.run(**self._build_payload(request))
        return to_response(self.name, request, model_to_dict(raw))

    async def _arun(self, request: LiveDataRequest) -> LiveDataResponse:
        with _unknown_live_data_source_named(self.name, request.source):
            raw = await self._within_deadline(self._async_client).extract.templates.run(**self._build_payload(request))
        return to_response(self.name, request, model_to_dict(raw))

    def _list(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        raw = self._sync_client.extract.templates.list(**compact({"limit": limit, "offset": offset}))
        return to_page(self.name, model_to_dict(raw))

    async def _alist(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        raw = await self._async_client.extract.templates.list(**compact({"limit": limit, "offset": offset}))
        return to_page(self.name, model_to_dict(raw))

    def _get(self, name: str) -> LiveDataSource:
        with _unknown_live_data_source_named(self.name, name):
            raw = self._sync_client.extract.templates.get(name)
        return to_source(self.name, model_to_dict(raw))

    async def _aget(self, name: str) -> LiveDataSource:
        with _unknown_live_data_source_named(self.name, name):
            raw = await self._async_client.extract.templates.get(name)
        return to_source(self.name, model_to_dict(raw))

    def _build_payload(self, request: LiveDataRequest) -> dict[str, Any]:
        """Translate a normalized request into Nimble ``extract.templates.run()`` keyword arguments.

        ``formats`` is sent only when a format was asked for: Nimble returns no
        page content without it, which is what an unset format means.
        """
        return build_payload(
            request,
            template=request.source,
            params=dict(request.inputs),
            formats=[NIMBLE_EXTRACT_FORMATS[request.format]] if request.format else None,
        )


@contextmanager
def _unknown_live_data_source_named(provider: str, source: str) -> Iterator[None]:
    """Turn Nimble's 404 for a source name into :class:`LiveDataSourceNotFoundError`, on a run or a describe."""
    try:
        yield
    except NotFoundError as exc:
        raise LiveDataSourceNotFoundError(provider, source, original=exc) from exc
