"""A live data catalogue's types: a source, a sample run of it, and one page of a vendor's sources.

Kept apart from :mod:`web_router.types_live_data`, which re-exports them beside
the request and the response.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from web_router.live_data_compat import warn_renamed


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
    version: int | str | None = Field(
        default=None,
        description="The published version, as the backend numbers it (Apify's build numbers are strings).",
    )
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
    query: str | None = Field(default=None, description="The keywords the page was searched with; None for a listing.")
    search: Literal["native", "local"] | None = Field(
        default=None,
        description=(
            "How `query` was matched: by the vendor's own search ('native', in its relevance order), or here "
            "over the whole catalogue on site, kind of page and description ('local'). None for a listing, and for a "
            "page from several backends that did not all search the same way."
        ),
    )
    raw: Any = Field(default=None, description="The provider's untouched response payload, object or array.")
    failures: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "On a page drawn from every configured backend (provider 'auto'), each backend that could not answer, "
            "with why. Its sources are missing from the page. Empty otherwise."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _templates_is_sources(cls, values: Any) -> Any:
        # A custom provider written against the old names still builds TemplatePage(templates=[...]).
        if isinstance(values, dict) and "templates" in values and "sources" not in values:
            warn_renamed("templates", "sources", stacklevel=3, field=True)
            values = {**values, "sources": values["templates"]}
            del values["templates"]
        return values

    def __len__(self) -> int:
        return len(self.sources)

    @property
    def names(self) -> list[str]:
        """Every source name on the page, in the backend's order."""
        return [source.name for source in self.sources]

    @property
    def templates(self) -> list[LiveDataSource]:
        """Deprecated: :attr:`sources`."""
        warn_renamed("templates", "sources", stacklevel=3, field=True)
        return self.sources
