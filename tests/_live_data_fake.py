"""An in-memory live data backend, so router and MCP tests never touch the network.

Kept beside ``tests/conftest.py``, which registers it as ``fake`` through the
``fake_live_data_provider`` fixture; moved out of it to keep that file in its
size budget.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.providers.base import LiveDataProvider
from web_router.types import ExtractedDocument, ExtractFormat
from web_router.types_live_data import (
    LIVE_DATA_PARAMETERS,
    LiveDataRequest,
    LiveDataResponse,
    LiveDataSample,
    LiveDataSource,
    LiveDataSourcePage,
)

FAKE_LIVE_DATA_SOURCE_NAMES = ("books_pdp", "jobs_listing")
"""The two templates the fake backend holds."""


class FakeLiveDataProvider(LiveDataProvider):
    """An in-memory Extract Template backend so router tests never touch the network.

    Holds a two-template catalogue, describes either, and runs any name it is
    given with a record echoing the inputs, so a test can assert on what
    reached it. ``unknown_names`` raises on a name, as a real backend's 404 would.
    """

    name = "fake"
    supported_parameters = LIVE_DATA_PARAMETERS
    supported_formats: frozenset[ExtractFormat] = frozenset({"markdown", "html"})

    unknown_names: ClassVar[frozenset[str]] = frozenset()
    run_raises: ClassVar[Exception | None] = None
    """What every run raises instead of answering, so a chain test sets a member's behaviour on its class."""
    list_raises: ClassVar[Exception | None] = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.requests: list[LiveDataRequest] = []
        self.listed: list[tuple[int | None, int | None]] = []
        self.described: list[str] = []
        self.closed = False

    def _describe(self, name: str) -> LiveDataSource:
        if name in self.unknown_names:
            raise RuntimeError(f"HTTP 404: no template {name}")
        return LiveDataSource(
            provider=self.name,
            name=name,
            description=f"what {name} extracts",
            domain="example.com",
            entity_type="Product Detail Page (PDP)",
            input_schema={"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"title": {"type": "string"}}},
            samples=[LiveDataSample(input={"url": "https://example.com/p/1"}, output={"title": "A"})],
            version=1,
            published=True,
            raw={"name": name},
        )

    def _respond(self, request: LiveDataRequest) -> LiveDataResponse:
        self.requests.append(request)
        if self.run_raises is not None:
            raise self.run_raises
        document = (
            ExtractedDocument(url="https://example.com/page", content="# Page", format=request.format)
            if request.format
            else None
        )
        return LiveDataResponse(
            provider=self.name,
            source=request.source,
            inputs=dict(request.inputs),
            data={"title": "Example", "inputs": dict(request.inputs)},
            url="https://example.com/page",
            document=document,
            request_id="task-1",
            raw={"source": request.source, "inputs": dict(request.inputs)},
        )

    def _run(self, request: LiveDataRequest) -> LiveDataResponse:
        return self._respond(request)

    async def _arun(self, request: LiveDataRequest) -> LiveDataResponse:
        return self._respond(request)

    def _list(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        self.listed.append((limit, offset))
        if self.list_raises is not None:
            raise self.list_raises
        return LiveDataSourcePage(
            provider=self.name,
            sources=[self._describe(name) for name in FAKE_LIVE_DATA_SOURCE_NAMES],
            total=len(FAKE_LIVE_DATA_SOURCE_NAMES),
            offset=offset,
            limit=limit,
            raw={"items": list(FAKE_LIVE_DATA_SOURCE_NAMES)},
        )

    async def _alist(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._list(limit=limit, offset=offset)

    def _get(self, name: str) -> LiveDataSource:
        self.described.append(name)
        return self._describe(name)

    async def _aget(self, name: str) -> LiveDataSource:
        return self._get(name)

    def close(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.closed = True
