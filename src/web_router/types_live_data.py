"""The types of live data: a vendor's ready-made scraper for one site, run for this call.

A live data source is a vendor's saved, site-specific extraction -- Nimble's
Extract Templates, Apify's Actors, Bright Data's scrapers, Oxylabs' sources.
Given its own inputs (a product id, a URL, a search term) it fetches the site
*now* and answers with structured records shaped by its output schema. That
is the rule a backend joins under: it fetches on request. Data a vendor
collected earlier and serves from storage is a vendor's own API, reached
through :meth:`~web_router.WebRouter.call_vendor`, never through here.

Kept out of :mod:`web_router.types`, which is at its size limit. The names
these types had when the method was ``run_template`` are kept as deprecated
aliases in :mod:`web_router.live_data_compat`.
"""

from __future__ import annotations

import warnings
from datetime import datetime
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from web_router.constants_live_data import MSG_LIVE_DATA_DEPRECATED_FIELD
from web_router.types import ExtractedDocument, ExtractFormat, NormalizedRequest


def _deprecated(old: str, new: str) -> None:
    warnings.warn(MSG_LIVE_DATA_DEPRECATED_FIELD.format(old=old, new=new), DeprecationWarning, stacklevel=3)


class LiveDataRequest(NormalizedRequest):
    """A normalized live data run: one source, its inputs, and the page format to return beside the records.

    The inputs are the source's, not ours: :attr:`inputs` is whatever its
    ``input_schema`` names, a URL for some sources and an id, a slug or a
    search term for others, so nothing here presumes a URL.

    :attr:`format` left as ``None`` means no page content beside the records,
    which is what every backend returns when asked for nothing: unlike an
    extract there is no default format to pin, because the records are the
    answer and the page is an extra.
    """

    always_sent: ClassVar[frozenset[str]] = frozenset({"source", "inputs", "provider_options"})

    source: Annotated[str, Field(min_length=1, description="The source's name, as the backend knows it.")]
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="The source's inputs, named by its input_schema -- a URL, an id, a search term.",
    )
    format: ExtractFormat | None = Field(
        default=None,
        description="A representation of the page to return beside the records. Unset returns the records alone.",
    )

    @model_validator(mode="before")
    @classmethod
    def _template_is_source(cls, values: Any) -> Any:
        # `template=` is what a run was called before the rename; it still works, with a warning.
        if isinstance(values, dict) and "template" in values and "source" not in values:
            _deprecated("template", "source")
            values = {**values, "source": values["template"]}
            del values["template"]
        return values

    @property
    def template(self) -> str:
        """Deprecated: :attr:`source`."""
        _deprecated("template", "source")
        return self.source


LIVE_DATA_ALWAYS_SENT = LiveDataRequest.always_sent
"""Fields every live data provider handles itself: the source, its inputs, and the untranslated passthrough."""

LIVE_DATA_PARAMETERS: frozenset[str] = LiveDataRequest.normalized_parameters()
"""Every optional parameter a live data provider may declare support for."""


class LiveDataSample(BaseModel):
    """One run the backend recorded for a source: inputs it accepted, and the output they produced.

    The shortest route to running a source you have not run before: copy
    :attr:`input` and change the values. Published by the backend, never made
    up here; a source with none has an empty :attr:`LiveDataSource.samples`.
    """

    # Unknown keys are a provider bug: a sample is exactly an input and an output.
    model_config = ConfigDict(extra="forbid")

    input: dict[str, Any] = Field(default_factory=dict, description="Inputs a run accepted, shaped by input_schema.")
    output: Any = Field(default=None, description="What that run returned, shaped by output_schema.")


class LiveDataSource(BaseModel):
    """One live data source a backend holds, as the backend describes it.

    What a caller needs to run it is :attr:`input_schema`, and what comes back
    is shaped by :attr:`output_schema`. Both are ``None`` for a source that
    has no published version yet, which cannot be run, or whose backend
    publishes no schema. :attr:`samples` are runs the backend recorded, an
    input and what it produced, when it publishes any.
    """

    # Unknown keys are a provider bug: anything unmapped belongs in `extra`.
    model_config = ConfigDict(extra="forbid")

    provider: str
    name: str = Field(description="The stable name a run names the source by.")
    description: str = Field(default="", description="What the source extracts, when the backend says.")
    domain: str | None = Field(default=None, description="The site the source reads, when the backend says.")
    entity_type: str | None = Field(default=None, description="The kind of page it reads, in the backend's words.")
    input_schema: dict[str, Any] | None = Field(
        default=None,
        description="JSON Schema of the inputs a run passes as `inputs`. None when the backend publishes none.",
    )
    output_schema: dict[str, Any] | None = Field(
        default=None,
        description="JSON Schema of each record a run returns. None when the backend publishes none.",
    )
    samples: list[LiveDataSample] = Field(
        default_factory=list,
        description="Runs the backend recorded, each an input and what it returned; empty when it has none.",
    )
    version: int | None = Field(default=None, description="The published version's number, when there is one.")
    published: bool = Field(default=False, description="Whether a version is published, so the source can be run.")
    created_at: datetime | None = None
    updated_at: datetime | None = None
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific fields with no normalized equivalent.",
    )
    raw: dict[str, Any] | None = Field(default=None, description="The provider's untouched description of it.")


class LiveDataSourcePage(BaseModel):
    """One page of a vendor's live data sources, tagged with the provider that served it.

    Backends page their catalogues, so this carries where in the catalogue the
    page sits beside the sources on it; :attr:`total` is the backend's count
    of the whole catalogue, when it reports one.
    """

    # Unknown keys are a provider bug: the untouched payload belongs in `raw`.
    model_config = ConfigDict(extra="forbid")

    provider: str
    sources: list[LiveDataSource] = Field(default_factory=list)
    total: int | None = Field(default=None, description="How many sources the backend holds, when it says.")
    offset: int | None = Field(default=None, description="How many sources precede this page, when it says.")
    limit: int | None = Field(default=None, description="The page size the backend applied, when it says.")
    raw: dict[str, Any] | None = Field(default=None, description="The provider's untouched response payload.")

    def __len__(self) -> int:
        return len(self.sources)

    @property
    def names(self) -> list[str]:
        """Every source name on the page, in the backend's order."""
        return [source.name for source in self.sources]

    @property
    def templates(self) -> list[LiveDataSource]:
        """Deprecated: :attr:`sources`."""
        _deprecated("templates", "sources")
        return self.sources


class LiveDataResponse(BaseModel):
    """The result of one live data run, tagged with the provider that served it.

    :attr:`records` are what the source extracted, each shaped by its
    ``output_schema``: one for a product page, many for a search or a listing.
    :attr:`data` is the first of them, for the sources that answer with one.
    :attr:`fetched_at` is when the backend says it fetched the page, and
    ``None`` when it does not say -- never the time this library received the
    answer. :attr:`document` is the page the records were parsed from, only
    when a :attr:`~LiveDataRequest.format` asked for it. A run the backend
    could not complete raises rather than coming back with no records.
    """

    # Unknown keys are a provider bug: the untouched payload belongs in `raw`.
    model_config = ConfigDict(extra="forbid")

    provider: str
    source: str
    inputs: dict[str, Any] = Field(default_factory=dict, description="The inputs the run was given, as sent.")
    records: list[dict[str, Any]] = Field(
        default_factory=list, description="Every record the run extracted, in the backend's order."
    )
    data: dict[str, Any] = Field(
        default_factory=dict, description="The first record, for sources that answer with one; {} when none came back."
    )
    fetched_at: datetime | None = Field(
        default=None, description="When the backend says it fetched the page; None when it does not say."
    )
    url: str | None = Field(default=None, description="The page the backend resolved the inputs to, when it says.")
    document: ExtractedDocument | None = Field(
        default=None,
        description="The page the records came from, in the format asked for; None when none was.",
    )
    status_code: int | None = Field(default=None, description="The fetched page's HTTP status, when reported.")
    request_id: str | None = Field(default=None, description="The provider's id for the request.")
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific fields with no normalized equivalent -- pagination hints, say.",
    )
    raw: Any = Field(default=None, description="The provider's untouched response payload.")

    @model_validator(mode="before")
    @classmethod
    def _one_record_or_many(cls, values: Any) -> Any:
        # A provider sets `records`, or `data` alone for a source that answers with one; the other follows.
        if not isinstance(values, dict):
            return values
        if "template" in values and "source" not in values:
            _deprecated("template", "source")
            values = {**values, "source": values.pop("template")}
        records, data = values.get("records"), values.get("data")
        if not records and data:
            values = {**values, "records": [data]}
        elif records and not data:
            values = {**values, "data": records[0]}
        return values

    @property
    def template(self) -> str:
        """Deprecated: :attr:`source`."""
        _deprecated("template", "source")
        return self.source
