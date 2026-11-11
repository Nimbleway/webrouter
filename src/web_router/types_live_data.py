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

from datetime import datetime
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from web_router.live_data_compat import warn_renamed
from web_router.types import ExtractedDocument, ExtractFormat, NormalizedRequest
from web_router.types_live_data_source import LiveDataSample as LiveDataSample
from web_router.types_live_data_source import LiveDataSource as LiveDataSource
from web_router.types_live_data_source import LiveDataSourcePage as LiveDataSourcePage


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
            warn_renamed("template", "source", stacklevel=3, field=True)
            values = {**values, "source": values["template"]}
            del values["template"]
        return values

    @property
    def template(self) -> str:
        """Deprecated: :attr:`source`."""
        warn_renamed("template", "source", stacklevel=3, field=True)
        return self.source


LIVE_DATA_ALWAYS_SENT = LiveDataRequest.always_sent
"""Fields every live data provider handles itself: the source, its inputs, and the untranslated passthrough."""

LIVE_DATA_PARAMETERS: frozenset[str] = LiveDataRequest.normalized_parameters()
"""Every optional parameter a live data provider may declare support for."""


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
            warn_renamed("template", "source", stacklevel=3, field=True)
            values = {**{k: v for k, v in values.items() if k != "template"}, "source": values["template"]}
        records, data = values.get("records"), values.get("data")
        if not records and data:
            values = {**values, "records": [data]}
        elif records and not data:
            values = {**values, "data": records[0]}
        return values

    @property
    def template(self) -> str:
        """Deprecated: :attr:`source`."""
        warn_renamed("template", "source", stacklevel=3, field=True)
        return self.source
