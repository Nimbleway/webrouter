"""Provider-agnostic request and response models.

Every provider maps its own payload onto these types, so swapping Nimble for
Tavily or You.com does not change the shape of what callers get back. Anything a
provider returns that has no home here is preserved verbatim under
``SearchResult.extra`` / ``SearchResponse.raw`` for a search, and under
``ExtractedDocument.extra`` / ``ExtractResponse.raw`` for an extract, and under
``TemplateResponse.extra`` / ``TemplateResponse.raw`` for an Extract Template run.

A browser session is the exception to "a call returns an answer": it returns a
:class:`BrowserSession` describing a browser the caller then drives itself.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sized
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Generic, Literal, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_serializer, model_validator

from web_router.types_decision import DECISION_DESCRIPTION, DecisionRecord

SearchDepth = Literal["lite", "fast", "deep"]
"""How much page content to retrieve per result.

- ``lite``: title, URL and description only — cheapest, best at high volume.
- ``fast``: a truncated page body, enough for most agent reasoning.
- ``deep``: full page content; slowest and most expensive.

Providers express this differently; each one maps these three levels onto its
own vocabulary.
"""


ExtractFormat = Literal["markdown", "html", "text"]
"""Which representation of a page to return.

- ``markdown``: the page as markdown -- structure kept, markup dropped.
- ``html``: the page's HTML, as the backend fetched it.
- ``text``: the visible text, with no markup at all.

Backends do not all offer all three: one that cannot produce the format you
asked for raises :class:`~web_router.exceptions.UnsupportedFormatError` rather
than returning a different one under the same name. See the format table in
README.md.
"""


class Provider(str, Enum):
    """The providers built into web-router.

    Every argument that names a provider also accepts a plain string; this
    exists so the built-in names are discoverable from an editor rather than
    remembered. Backends added with :func:`web_router.register_provider` are
    named by string, which is why the string form is never going away.

    Not every name serves every method: ``oxylabs``, ``brightdata``,
    ``firecrawl`` and ``jina`` extract but do not search, and most of the search
    backends do not extract. ``steel``, ``browserbase``, ``hyperbrowser``,
    ``anchor``, ``kernel`` and ``local`` open browser sessions and do nothing else, and
    ``browser-use`` and ``skyvern`` run browse tasks and nothing else.
    :func:`web_router.available_providers`,
    :func:`web_router.available_extract_providers` and their research and
    browser counterparts list each method's own set. ``auto`` serves every
    method by trying its backends in turn; ``max`` serves extract alone, by
    trying every backend at once and returning the first document back.
    """

    NIMBLE = "nimble"
    TAVILY = "tavily"
    YOU = "you"
    BRAVE = "brave"
    EXA = "exa"
    PARALLEL = "parallel"
    PERPLEXITY = "perplexity"
    LINKUP = "linkup"
    WEBIQ = "webiq"
    KEENABLE = "keenable"
    DEEPSEEK = "deepseek"
    AGENTCORE = "agentcore"
    YOU_MCP = "you-mcp"
    EXA_MCP = "exa-mcp"
    PARALLEL_MCP = "parallel-mcp"
    OXYLABS = "oxylabs"
    BRIGHTDATA = "brightdata"
    FIRECRAWL = "firecrawl"
    JINA = "jina"
    STEEL = "steel"
    BROWSERBASE = "browserbase"
    HYPERBROWSER = "hyperbrowser"
    ANCHOR = "anchor"
    KERNEL = "kernel"
    SKYVERN = "skyvern"
    LOCAL = "local"
    BROWSER_USE = "browser-use"
    AUTO = "auto"
    MAX = "max"

    # A str-mixin enum otherwise renders as "Provider.NIMBLE" in f-strings and
    # str(), which would reach users through error messages and `raw` payloads.
    __str__ = str.__str__


def provider_name(provider: str | Provider) -> str:
    """Normalize a provider argument to the plain lowercase name used for lookup."""
    return str(provider).lower()


class NormalizedRequest(BaseModel):
    """What every normalized request has in common.

    Each method has its own request model -- :class:`SearchRequest`,
    :class:`ExtractRequest` -- and they share only this: a set of optional
    parameters a provider may or may not map, and an untranslated passthrough.
    :meth:`SearchProvider.validate <web_router.SearchProvider.validate>` reads
    :meth:`specified_parameters` to reject a request carrying options the chosen
    backend does not map.
    """

    model_config = ConfigDict(extra="forbid")

    always_sent: ClassVar[frozenset[str]]
    """Fields the provider handles itself, so never an option it declares support for."""

    provider_options: dict[str, Any] = Field(
        default_factory=dict,
        description="Passed straight through to the underlying provider call, untranslated.",
    )

    @classmethod
    def normalized_parameters(cls) -> frozenset[str]:
        """Every optional parameter a provider may declare support for.

        Derived from the model rather than listed by hand, so adding a field is
        enough to make it part of the shared surface.
        """
        return frozenset(cls.model_fields) - cls.always_sent

    def specified_parameters(self) -> set[str]:
        """Names of the optional parameters this request actually sets."""
        return {name for name in self.normalized_parameters() if getattr(self, name) is not None}


MAX_RESULTS_DESCRIPTION = (
    "How many results to return; unset, the backend's own default. For an agent: 5 to 10 for most questions,"
    " more for news or a question with several parts, and 1 only when the user asked for a single link."
)
"""What a model reads about ``max_results``, in this schema and the MCP server's."""


class SearchRequest(NormalizedRequest):
    """A normalized search request.

    Only :attr:`query` is required. Fields left as ``None`` are omitted from the
    provider call so each backend applies its own default rather than ours.
    """

    always_sent: ClassVar[frozenset[str]] = frozenset({"query", "provider_options"})

    query: Annotated[str, Field(min_length=1, description="The search query.")]
    max_results: Annotated[int | None, Field(default=None, ge=1, description=MAX_RESULTS_DESCRIPTION)] = None
    search_depth: SearchDepth | None = Field(default=None, description="How much page content to retrieve.")
    include_domains: list[str] | None = Field(default=None, description="Restrict results to these domains.")
    exclude_domains: list[str] | None = Field(default=None, description="Drop results from these domains.")
    country: str | None = Field(default=None, description="Country code for geo-targeting, e.g. 'US'.")
    locale: str | None = Field(default=None, description="Language code, e.g. 'en'.")


ALWAYS_SENT = SearchRequest.always_sent
"""Fields every search provider handles itself: the query, and the untranslated passthrough."""

NORMALIZED_PARAMETERS: frozenset[str] = SearchRequest.normalized_parameters()
"""Every optional parameter a search provider may declare support for."""


class ExtractRequest(NormalizedRequest):
    """A normalized extract request.

    Only :attr:`urls` is required. :attr:`format` left as ``None`` means
    ``EXTRACT_DEFAULT_FORMAT`` (markdown) from every backend that can return it,
    and that backend's own default from one that cannot (Exa's text);
    :attr:`ExtractedDocument.format` names which came back.
    """

    always_sent: ClassVar[frozenset[str]] = frozenset({"urls", "provider_options"})

    urls: Annotated[
        list[Annotated[str, Field(min_length=1)]],
        Field(min_length=1, description="The URLs to fetch, in the order the documents come back."),
    ]
    format: ExtractFormat | None = Field(default=None, description="Which representation of each page to return.")


EXTRACT_ALWAYS_SENT = ExtractRequest.always_sent
"""Fields every extract provider handles itself: the URLs, and the untranslated passthrough."""

EXTRACT_PARAMETERS: frozenset[str] = ExtractRequest.normalized_parameters()
"""Every optional parameter an extract provider may declare support for."""


class SearchResult(BaseModel):
    """A single result, normalized across providers."""

    # Unknown keys are a provider bug: anything unmapped belongs in `extra`.
    model_config = ConfigDict(extra="forbid")

    url: str
    title: str = ""
    description: str = Field(
        default="",
        description=(
            "A snippet-length summary: the provider's own when it gives one, otherwise "
            "the head of the page body so the field means the same thing everywhere."
        ),
    )
    content: str = Field(
        default="",
        description=(
            "The fullest text available for this result: the page body when the provider "
            "returned one, otherwise the description. Empty only when there is no text at all."
        ),
    )
    score: float | None = Field(default=None, description="Provider relevance score, when one is given.")
    published_date: str | None = None
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific fields with no normalized equivalent.",
    )


class SearchResponse(BaseModel):
    """The result of one search, tagged with the provider that served it."""

    # Unknown keys are a provider bug: the untouched payload belongs in `raw`.
    model_config = ConfigDict(extra="forbid")

    provider: str
    query: str
    results: list[SearchResult] = Field(default_factory=list)
    answer: str | None = Field(default=None, description="Synthesized answer, when the provider returned one.")
    request_id: str | None = None
    raw: dict[str, Any] | None = Field(default=None, description="The provider's untouched response payload.")
    decision: DecisionRecord | None = Field(default=None, description=DECISION_DESCRIPTION)

    def __len__(self) -> int:
        return len(self.results)

    @property
    def urls(self) -> list[str]:
        """Every result URL, in rank order."""
        return [result.url for result in self.results]


class ExtractedDocument(BaseModel):
    """One fetched page, normalized across providers."""

    # Unknown keys are a provider bug: anything unmapped belongs in `extra`.
    model_config = ConfigDict(extra="forbid")

    url: str
    title: str = Field(default="", description="The page title, when the provider returned one.")
    content: str = Field(default="", description="The page body, in the representation :attr:`format` names.")
    format: ExtractFormat | None = Field(
        default=None,
        description=(
            "Which representation :attr:`content` holds -- the one asked for, or, when none was, "
            "markdown wherever the backend can return it and its own default where it cannot. "
            "Never a guess: a provider that cannot return the requested format raises instead."
        ),
    )
    status_code: int | None = Field(default=None, description="The fetched page's HTTP status, when reported.")
    published_date: str | None = None
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific fields with no normalized equivalent.",
    )


class ExtractFailure(BaseModel):
    """A URL the provider could not fetch, and why."""

    model_config = ConfigDict(extra="forbid")

    url: str
    error: str


class ExtractResponse(BaseModel):
    """The result of one extract call, tagged with the provider that served it.

    Extracting several URLs can partly succeed: what came back is in
    :attr:`documents`, and every URL that did not is in :attr:`failures` with
    the reason. A call where *nothing* came back raises instead, so a failure
    is never something you have to go looking for.
    """

    # Unknown keys are a provider bug: the untouched payload belongs in `raw`.
    model_config = ConfigDict(extra="forbid")

    provider: str
    documents: list[ExtractedDocument] = Field(default_factory=list)
    failures: list[ExtractFailure] = Field(default_factory=list)
    request_id: str | None = Field(
        default=None,
        description=(
            "The provider's id for the request. Backends that take one URL per call issue one id "
            "per document; the first is reported here and all of them are in :attr:`raw`."
        ),
    )
    raw: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "The provider's untouched response payloads, one per API call -- a single entry for a "
            "backend that takes every URL at once, one per URL for a backend that does not."
        ),
    )
    decision: DecisionRecord | None = Field(default=None, description=DECISION_DESCRIPTION)

    def __len__(self) -> int:
        return len(self.documents)

    @property
    def urls(self) -> list[str]:
        """Every document URL, in the order they were asked for."""
        return [document.url for document in self.documents]


_ResponseT = TypeVar("_ResponseT", bound=BaseModel)
_RowT = TypeVar("_RowT", bound="_BenchmarkRow[Any]")
_SelfRowT = TypeVar("_SelfRowT", bound="_BenchmarkRow[Any]")


class _BenchmarkRow(BaseModel, Generic[_ResponseT]):
    """One provider's row in a comparison: what the same request got from it, and how fast.

    Exactly one of :attr:`response` and :attr:`error` is set. The error is the
    :class:`~web_router.WebRouterError` the call raised -- a refused option, a
    backend failure, or a :class:`~web_router.BenchmarkTimeoutError` -- kept as
    the exception so a caller can tell them apart, and serialized as its
    message. (Typed as ``Exception`` only because this module sits below the
    exceptions in the import graph.)

    Shared by :class:`BenchmarkRun` and :class:`ExtractBenchmarkRun`, which
    differ only in the response they carry.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    provider: str
    response: _ResponseT | None = Field(default=None, description="The normalized answer, when the call succeeded.")
    error: Exception | None = Field(default=None, description="What the call raised instead, when it did not.")
    latency: float = Field(
        ge=0.0, description="Wall-clock seconds around the public call, until it answered or was given up on."
    )

    @classmethod
    def of(cls: type[_SelfRowT], provider: str, started: float | None, outcome: Any) -> _SelfRowT:
        """A row from its own start: the call's latency, not the comparison's; none for a row that never started.

        ``outcome`` is the response the call returned or the exception it
        raised -- the one place a fix to latency or error handling lands for
        both methods' rows.
        """
        latency = 0.0 if started is None else time.perf_counter() - started
        if isinstance(outcome, Exception):
            return cls(provider=provider, error=outcome, latency=latency)
        return cls(provider=provider, response=outcome, latency=latency)

    @field_serializer("error")
    def _serialize_error(self, error: Exception | None) -> str | None:
        return None if error is None else str(error)

    @model_validator(mode="after")
    def _one_outcome(self) -> _BenchmarkRow[_ResponseT]:
        # Inline rather than in constants.py, which imports this module.
        if (self.response is None) == (self.error is None):
            raise ValueError("a benchmark row carries exactly one of response and error")
        return self

    @property
    def ok(self) -> bool:
        """Whether the call answered."""
        return self.error is None

    @property
    def count(self) -> int:
        """How much came back -- results for a search, documents for an extract; zero when the call did not answer."""
        # Both responses count themselves; the bound is BaseModel only so pydantic can build the base.
        return 0 if self.response is None else len(cast(Sized, self.response))


class _BenchmarkRows(Generic[_RowT]):
    """The ways of reading a comparison's rows that every report shares.

    A mixin rather than a model with the field: each report declares ``runs``
    itself, after what names the comparison, so the serialized order stays
    ``query`` (or ``urls``) then ``runs``, as it was before the two reports
    shared anything. The annotation below is for the type checker alone --
    pydantic reads a base's annotations as fields, order included.
    """

    if TYPE_CHECKING:
        runs: list[_RowT]

    def __len__(self) -> int:
        return len(self.runs)

    def __getitem__(self, provider: str) -> _RowT:
        """The row for ``provider``."""
        for run in self.runs:
            if run.provider == provider:
                return run
        raise KeyError(provider)

    @property
    def succeeded(self) -> list[_RowT]:
        """The rows whose call answered."""
        return [run for run in self.runs if run.ok]

    @property
    def failed(self) -> list[_RowT]:
        """The rows whose call raised instead."""
        return [run for run in self.runs if not run.ok]


class BenchmarkRun(_BenchmarkRow[SearchResponse]):
    """One provider's row in a :class:`BenchmarkReport`: what the same query got from it, and how fast."""


class BenchmarkReport(BaseModel, _BenchmarkRows[BenchmarkRun]):
    """The same query across several providers: one :class:`BenchmarkRun` per provider, in the order asked.

    Measured facts only. Latency is measured, errors are caught and reported,
    results are counted -- there is no quality score, relevance estimate or
    price, because judging results against a task is the caller's job and any
    price table shipped here would drift. The full responses ride along so
    that judgment can be applied.
    """

    model_config = ConfigDict(extra="forbid")

    query: str
    runs: list[BenchmarkRun] = Field(default_factory=list)


class ExtractBenchmarkRun(_BenchmarkRow[ExtractResponse]):
    """One provider's row in an :class:`ExtractBenchmarkReport`: what the same URLs got from it, and how fast.

    :attr:`count` is the documents that came back. A call that fetched some of
    the URLs and not others is still an answered row -- its
    :attr:`~web_router.ExtractResponse.failures` say which -- exactly as the
    :meth:`~web_router.WebRouter.extract` call it measures stays partly
    successful; only a call where nothing came back is the row's error.
    """


class ExtractBenchmarkReport(BaseModel, _BenchmarkRows[ExtractBenchmarkRun]):
    """The same URLs across several providers: one :class:`ExtractBenchmarkRun` per provider, in the order asked.

    Measured facts only, as a :class:`BenchmarkReport` is: latency around the
    call, the documents that came back and the URLs that did not, and the full
    response for judging. Whether a page's text is complete or correct is the
    caller's judgment, not a column here.
    """

    model_config = ConfigDict(extra="forbid")

    urls: list[str]
    runs: list[ExtractBenchmarkRun] = Field(default_factory=list)


class TemplateRequest(NormalizedRequest):
    """A normalized Extract Template run.

    A template is a reusable extraction a backend holds for one kind of page --
    a retailer's product page, a job board's listing -- that turns the page
    into a record shaped by the template's own output schema. Its inputs are
    the template's, not ours: :attr:`inputs` is whatever its ``input_schema``
    names, a URL for some templates and an id, a slug or a search term for
    others, so nothing here presumes a URL.

    :attr:`format` left as ``None`` means no page content beside the record,
    which is what every backend returns when asked for nothing: unlike an
    extract there is no default format to pin, because the record is the
    answer and the page is an extra.
    """

    always_sent: ClassVar[frozenset[str]] = frozenset({"template", "inputs", "provider_options"})

    template: Annotated[str, Field(min_length=1, description="The template's name, as the backend knows it.")]
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="The template's inputs, named by its input_schema -- a URL, an id, a search term.",
    )
    format: ExtractFormat | None = Field(
        default=None,
        description="A representation of the page to return beside the record. Unset returns the record alone.",
    )


TEMPLATE_ALWAYS_SENT = TemplateRequest.always_sent
"""Fields every template provider handles itself: the template, its inputs, and the untranslated passthrough."""

TEMPLATE_PARAMETERS: frozenset[str] = TemplateRequest.normalized_parameters()
"""Every optional parameter a template provider may declare support for."""


class TemplateSample(BaseModel):
    """One run the backend recorded for a template: inputs it accepted, and the record they produced.

    The shortest route to running a template you have not run before: copy
    :attr:`input` and change the values. Published by the backend, never made
    up here; a template with none has an empty :attr:`Template.samples`.
    """

    # Unknown keys are a provider bug: a sample is exactly an input and an output.
    model_config = ConfigDict(extra="forbid")

    input: dict[str, Any] = Field(default_factory=dict, description="Inputs a run accepted, shaped by input_schema.")
    output: Any = Field(default=None, description="The record that run returned, shaped by output_schema.")


class Template(BaseModel):
    """One Extract Template a backend holds, as the backend describes it.

    What a caller needs to run it is :attr:`input_schema`, and what comes back
    is shaped by :attr:`output_schema`. Both are ``None`` for a template that
    has no published version yet, which cannot be run. :attr:`samples` are
    runs the backend recorded, an input and the record it produced, when it
    publishes any.
    """

    # Unknown keys are a provider bug: anything unmapped belongs in `extra`.
    model_config = ConfigDict(extra="forbid")

    provider: str
    name: str = Field(description="The stable name a run names the template by.")
    description: str = Field(default="", description="What the template extracts, when the backend says.")
    domain: str | None = Field(default=None, description="The site the template reads, when the backend says.")
    entity_type: str | None = Field(default=None, description="The kind of page it reads, in the backend's words.")
    input_schema: dict[str, Any] | None = Field(
        default=None,
        description=(
            "JSON Schema of the inputs a run passes as `inputs`. None for a template with no published version."
        ),
    )
    output_schema: dict[str, Any] | None = Field(
        default=None,
        description="JSON Schema of the record a run returns. None for a template with no published version.",
    )
    samples: list[TemplateSample] = Field(
        default_factory=list,
        description="Runs the backend recorded, each an input and the record it returned; empty when it has none.",
    )
    version: int | None = Field(default=None, description="The published version's number, when there is one.")
    published: bool = Field(default=False, description="Whether a version is published, so the template can be run.")
    created_at: datetime | None = None
    updated_at: datetime | None = None
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific fields with no normalized equivalent.",
    )
    raw: dict[str, Any] | None = Field(default=None, description="The provider's untouched description of it.")


class TemplatePage(BaseModel):
    """One page of a vendor's templates, tagged with the provider that served it.

    Backends page their catalogues, so this carries where in the catalogue the
    page sits beside the templates on it; :attr:`total` is the backend's count
    of the whole catalogue, when it reports one.
    """

    # Unknown keys are a provider bug: the untouched payload belongs in `raw`.
    model_config = ConfigDict(extra="forbid")

    provider: str
    templates: list[Template] = Field(default_factory=list)
    total: int | None = Field(default=None, description="How many templates the backend holds, when it says.")
    offset: int | None = Field(default=None, description="How many templates precede this page, when it says.")
    limit: int | None = Field(default=None, description="The page size the backend applied, when it says.")
    raw: dict[str, Any] | None = Field(default=None, description="The provider's untouched response payload.")

    def __len__(self) -> int:
        return len(self.templates)

    @property
    def names(self) -> list[str]:
        """Every template name on the page, in the backend's order."""
        return [template.name for template in self.templates]


class TemplateResponse(BaseModel):
    """The result of one Extract Template run, tagged with the provider that served it.

    :attr:`data` is the record the template parsed, shaped by its
    ``output_schema``; :attr:`document` is the page it was parsed from, only
    when a :attr:`~TemplateRequest.format` asked for it. A run the backend
    could not complete -- a page it could not fetch, a template that failed on
    it -- raises rather than coming back with an empty record.
    """

    # Unknown keys are a provider bug: the untouched payload belongs in `raw`.
    model_config = ConfigDict(extra="forbid")

    provider: str
    template: str
    inputs: dict[str, Any] = Field(default_factory=dict, description="The inputs the run was given, as sent.")
    data: dict[str, Any] = Field(
        default_factory=dict, description="The record, shaped by the template's output_schema."
    )
    url: str | None = Field(default=None, description="The page the backend resolved the inputs to, when it says.")
    document: ExtractedDocument | None = Field(
        default=None,
        description="The page the record came from, in the format asked for; None when none was.",
    )
    status_code: int | None = Field(default=None, description="The fetched page's HTTP status, when reported.")
    request_id: str | None = Field(default=None, description="The provider's id for the request.")
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific fields with no normalized equivalent -- pagination hints, say.",
    )
    raw: dict[str, Any] | None = Field(default=None, description="The provider's untouched response payload.")


ResearchEffort = Literal["low", "medium", "high", "max"]
"""How much work a research run should do before answering.

- ``low``: a quick pass, closest in cost to a deep search.
- ``medium``: the default shape of an agentic run.
- ``high``: several rounds of gathering and cross-checking.
- ``max``: everything the backend will spend on one question.

Backends name these tiers differently -- processors, effort levels, reasoning
budgets -- and some offer fewer than four. Each one maps these onto its own
vocabulary, and a backend with coarser tiers maps more than one level onto the
same setting rather than inventing a distinction it does not have.
"""

JobState = Literal["queued", "running", "completed", "failed", "cancelled"]
"""Where a job -- work the backend carries on with after the call that started it -- currently is.

``queued`` and ``running`` are the two non-terminal states; a job in any of the
other three will not change again. Every method that is a job shares it, so
what "finished" means cannot drift from one method to the next.
"""

ResearchState = JobState
"""Where a research run currently is: research is a job, so this is :data:`JobState`."""

ResearchOutcome = Literal["complete", "partial"]
"""Whether a finished run answered the whole question or stopped early.

``partial`` only when the backend itself said so -- an exhausted budget, a
truncated generation. A run that simply found little is ``complete``: it
finished the work it was asked to do.
"""


class ResearchRequest(NormalizedRequest):
    """A normalized research request.

    Only :attr:`query` is required. Setting :attr:`output_schema` asks for a
    structured answer instead of prose, and adding :attr:`records` on top of it
    turns the run into an enrichment: the same schema, filled in once per record
    you already hold.
    """

    always_sent: ClassVar[frozenset[str]] = frozenset({"query", "provider_options"})

    query: Annotated[str, Field(min_length=1, description="The question, or the instructions for the run.")]
    output_schema: dict[str, Any] | None = Field(
        default=None,
        description="JSON Schema the answer must conform to. Unset asks for prose instead.",
    )
    records: list[dict[str, Any]] | None = Field(
        default=None,
        description="Partial records to enrich, each shaped like `output_schema`. Unset researches the query alone.",
    )
    effort: ResearchEffort | None = Field(default=None, description="How much work the run should do.")
    include_domains: list[str] | None = Field(default=None, description="Restrict sources to these domains.")
    exclude_domains: list[str] | None = Field(default=None, description="Keep these domains out of the sources.")


RESEARCH_ALWAYS_SENT = ResearchRequest.always_sent
"""Fields every research provider handles itself: the query, and the untranslated passthrough."""

RESEARCH_PARAMETERS: frozenset[str] = ResearchRequest.normalized_parameters()
"""Every optional parameter a research provider may declare support for."""


class Citation(BaseModel):
    """A source the answer rests on."""

    # Unknown keys are a provider bug: anything unmapped belongs in `extra`.
    model_config = ConfigDict(extra="forbid")

    url: str
    title: str = Field(default="", description="The cited page's title, when the provider returned one.")
    snippet: str = Field(
        default="",
        description="The passage the backend quoted from the page, when it quoted one.",
    )
    field: str | None = Field(
        default=None,
        description=(
            "Which part of the answer this source backs -- a JSON path into `structured`, or a marker "
            "in `answer` (`[2]`, or on You.com the whole group that names it, `[[1, 2]]`) -- when the "
            "backend attributes per field. `None` when it ties the source to no part of it."
        ),
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific fields with no normalized equivalent.",
    )


class ResearchResponse(BaseModel):
    """The result of one research run, tagged with the provider that served it.

    A run answers in prose, in structure, or in both: :attr:`answer` holds the
    prose and :attr:`structured` the object matching the schema that was asked
    for. Which of them is set follows from the request, and
    :attr:`citations` backs whichever came back.
    """

    # Unknown keys are a provider bug: the untouched payloads belong in `raw`.
    model_config = ConfigDict(extra="forbid")

    provider: str
    query: str
    answer: str | None = Field(default=None, description="The prose answer, when the run produced one.")
    structured: dict[str, Any] | list[Any] | None = Field(
        default=None,
        description="The structured answer, conforming to the schema that was asked for.",
    )
    citations: list[Citation] = Field(default_factory=list)
    outcome: ResearchOutcome = Field(
        default="complete",
        description="Whether the backend finished the work or said it stopped early.",
    )
    request_id: str | None = Field(default=None, description="The backend's own id for the run.")
    raw: list[dict[str, Any]] | None = Field(
        default=None,
        description="The provider's untouched payloads, one per call the run took.",
    )
    decision: DecisionRecord | None = Field(default=None, description=DECISION_DESCRIPTION)

    @property
    def urls(self) -> list[str]:
        """Every cited URL, in the order the backend gave them, without repeats."""
        return list(dict.fromkeys(citation.url for citation in self.citations))


class ResearchProgress(BaseModel):
    """Where one research run stands, and its answer once it has one."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(description="The backend's id for this run, which is what a later poll is keyed on.")
    state: ResearchState
    response: ResearchResponse | None = Field(
        default=None,
        description="The answer. Set exactly when `state` is 'completed'.",
    )
    detail: str | None = Field(
        default=None,
        description="Whatever the backend said about this state -- an error message, a progress note.",
    )

    @property
    def is_terminal(self) -> bool:
        """Whether this run has stopped, one way or another."""
        return self.state not in ("queued", "running")


BrowserState = Literal["starting", "running", "released", "expired", "failed"]
"""Where a browser session currently is.

``starting`` and ``running`` are the two live states; a session in any of the
other three is gone and will not come back. ``released`` means somebody ended
it, ``expired`` that the backend ended it on a timeout.
"""


StealthLevel = Literal["off", "low", "high"]
"""How hard a browser backend works to hide that the session is automated.

- ``off``: no stealth, asked for explicitly -- for a backend that applies some by default.
- ``low``: the backend's standard stealth.
- ``high``: the strongest it has, which is often plan-gated.

Unset sends nothing, and the backend keeps its own default. There are three
because no backend has more than two stealth tiers; another level is added only
when a backend can tell it apart from these. A backend that cannot serve the
level asked for raises :class:`~web_router.exceptions.UnsupportedParameterError`
rather than serving another one under its name.
"""


class Viewport(BaseModel):
    """The size of the page a session renders, in CSS pixels."""

    model_config = ConfigDict(extra="forbid")

    width: Annotated[int, Field(ge=1, description="Width in pixels.")]
    height: Annotated[int, Field(ge=1, description="Height in pixels.")]


class BrowserRequest(NormalizedRequest):
    """A normalized request for a browser session.

    Every field is optional. Fields left as ``None`` are omitted from the
    provider call, with one exception: :attr:`timeout`, which the
    :class:`~web_router.BrowserProvider` fills in from
    :data:`~web_router.constants.BROWSER_TIMEOUT` when the backend maps it,
    because a session nobody remembered to end is billed until it times out.
    """

    always_sent: ClassVar[frozenset[str]] = frozenset({"provider_options"})

    proxy_country: str | None = Field(
        default=None,
        description="ISO 3166-1 alpha-2 code of the country the session's traffic exits from, in either case.",
    )
    stealth: StealthLevel | None = Field(default=None, description="How hard the backend hides the automation.")
    solve_captchas: bool | None = Field(default=None, description="Ask the backend to solve CAPTCHAs it meets.")
    profile: str | None = Field(default=None, description="The backend's id for a saved profile to start from.")
    persist_profile: bool | None = Field(
        default=None,
        description="Save the session's cookies and storage back to `profile` when it ends.",
    )
    timeout: Annotated[
        float | None,
        Field(default=None, gt=0, description="Seconds the session may live, however busy it is."),
    ] = None
    idle_timeout: Annotated[
        float | None,
        Field(default=None, gt=0, description="Seconds the session may sit unused before the backend ends it."),
    ] = None
    viewport: Viewport | None = Field(default=None, description="The size of the page the session renders.")
    headless: bool | None = Field(default=None, description="Run without a visible window.")


BROWSER_ALWAYS_SENT = BrowserRequest.always_sent
"""Fields every browser provider handles itself: only the untranslated passthrough."""

BROWSER_PARAMETERS: frozenset[str] = BrowserRequest.normalized_parameters()
"""Every optional parameter a browser provider may declare support for."""

FieldPath = tuple[str, ...]
"""Where a field sits in a JSON body: one key for a top-level field, one per level for a nested one."""


class _RequestValue(Enum):
    REQUEST_VALUE = "request-value"


REQUEST_VALUE = _RequestValue.REQUEST_VALUE
"""Stands for the request's own value inside a :class:`BrowserField`, wherever in the field it goes."""


@dataclass(frozen=True)
class BrowserField:
    """Every field one option sets in a browser API's body, for an option a single path cannot carry.

    A browser provider's field table maps each option it sends to one of three
    rows, and ``build_browser_payload`` places what each produces:

    - a path, ``("session", "timeout")``: the request's value goes there;
    - a lookup, ``{"low": {("useStealth",): True}, ...}``: the value picks the
      fields to set, and a value the lookup does not list is never sent;
    - a :class:`BrowserField`, which sets every field in :attr:`sets` --
      ``{("useProxy",): True, ("proxyCountry",): REQUEST_VALUE}`` turns a proxy
      on and names its country in one row.
    """

    sets: Mapping[FieldPath, Any]
    """Path -> what goes there: a literal, or :data:`REQUEST_VALUE`, alone or at any depth in a dict, list or tuple.

    Each value is placed whole, so no two paths may meet: one path inside
    another, or the same path twice across rows, is a table bug and raises.
    """

    case: Literal["upper", "lower"] | None = None
    """The case a text value is sent in -- a country code, say, for an API that takes one case only.

    Only for an option whose value is text; set on any other, it raises rather than being skipped.
    """


BrowserFieldRow = FieldPath | Mapping[Any, Mapping[FieldPath, Any]] | BrowserField
"""One option's row in a browser provider's field table: a path, a lookup, or a :class:`BrowserField`."""


class BrowserSession(BaseModel):
    """One open browser session, as the backend described it.

    :attr:`cdp_url` is a credential -- whoever holds it drives the browser, and
    some backends put the account's API key in it -- so it is a ``SecretStr``
    that prints as asterisks, and :attr:`raw`, which may repeat it, is kept out
    of both ``repr`` and ``model_dump()``. Read it with ``cdp_url.get_secret_value()``, or from
    :attr:`BrowserHandle.cdp_url <web_router.BrowserHandle.cdp_url>`. :attr:`control_url`
    is a credential for the same reason and kept the same way.
    """

    # Unknown keys are a provider bug: the untouched payload belongs in `raw`.
    model_config = ConfigDict(extra="forbid")

    provider: str
    session_id: str = Field(description="The backend's id for this session, which a release is keyed on.")
    cdp_url: SecretStr = Field(description="The Chrome DevTools Protocol endpoint to connect a driver to.")
    live_view_url: str | None = Field(
        default=None,
        description="A page that shows the session as it runs, when the backend offers one that grants no control.",
    )
    control_url: SecretStr | None = Field(
        default=None,
        description=(
            "A page where a person can see and drive the session -- to log in, say -- when the backend returns one "
            "on open. A credential, like `cdp_url`. Read it through the handle's `control_url()`, which also asks "
            "a backend that hands it out only on request."
        ),
    )
    expires_at: datetime | None = Field(
        default=None,
        description="When the backend will end the session on its own, when it says.",
    )
    raw: dict[str, Any] | None = Field(
        default=None,
        repr=False,
        # Out of model_dump() too, not just repr: a session serialized into a
        # log or an API response would otherwise carry the key-bearing URL.
        exclude=True,
        description="The provider's untouched response payload. Read it as an attribute; it is never serialized.",
    )


BrowseOutcome = Literal["complete", "partial"]
"""Whether a finished browse task did all it was asked, or said it stopped early.

``partial`` only when the backend itself said so, as for research. None of the
built-in task backends reports an early stop that can be told apart from a
finished run -- Skyvern's ``timed_out`` cannot distinguish its step limit from
its clock, and Browser Use's ``isSuccess`` is its agent's opinion of its own
answer -- so each of them answers ``complete``. Reading either signal as
``partial`` would be the router inventing a verdict the backend did not give.
"""


class BrowseRequest(NormalizedRequest):
    """A normalized browse task: instructions for an agent driving a hosted browser.

    Only :attr:`task` is required. :attr:`start_url` is optional in the model
    but not on every backend -- one that cannot find its own way to a first page
    raises :class:`~web_router.MissingParameterError` before the task starts
    rather than having the URL folded into the task text. There is no ``model``
    field: the LLM behind one vendor's agent is not comparable to another's, so
    it goes through ``provider_options``.
    """

    always_sent: ClassVar[frozenset[str]] = frozenset({"task", "provider_options"})

    task: Annotated[str, Field(min_length=1, description="What the agent should do, in plain words.")]
    start_url: str | None = Field(default=None, description="The page the agent starts on.")
    output_schema: dict[str, Any] | None = Field(
        default=None,
        description="JSON Schema the answer must conform to. Unset asks for prose instead.",
    )
    max_steps: Annotated[
        int | None,
        Field(default=None, ge=1, description="Upper bound on the actions the agent may take."),
    ] = None
    allowed_domains: Annotated[
        list[Annotated[str, Field(min_length=1)]] | None,
        # An empty list would read as "no domain allowed" to one backend and
        # "no restriction" to another, so it is refused rather than guessed at.
        Field(default=None, min_length=1, description="Keep the agent on these domains."),
    ] = None


BROWSE_ALWAYS_SENT = BrowseRequest.always_sent
"""Fields every browse provider handles itself: the task, and the untranslated passthrough."""

BROWSE_PARAMETERS: frozenset[str] = BrowseRequest.normalized_parameters()
"""Every optional parameter a browse provider may declare support for."""


class BrowseStep(BaseModel):
    """One action the agent reported taking.

    Only what the backend reported: a task whose backend returns no step list
    has an empty :attr:`BrowseResponse.steps`, never one reconstructed from the
    final answer. There is no step number -- the list is in the order the
    backend gave, and a backend's own numbering, when it has one, is in
    :attr:`extra`.
    """

    # Unknown keys are a provider bug: anything unmapped belongs in `extra`.
    model_config = ConfigDict(extra="forbid")

    action: str | None = Field(default=None, description="What the agent did, in the backend's words.")
    url: str | None = Field(default=None, description="The page the step happened on, when reported.")
    detail: str | None = Field(default=None, description="The agent's own note on the step -- its reasoning, a result.")
    screenshot_url: str | None = Field(default=None, description="A screenshot taken at this step, when reported.")
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific fields with no normalized equivalent.",
    )


class BrowseResponse(BaseModel):
    """The result of one browse task, tagged with the provider that served it.

    Like a research run, a task answers in prose, in structure, or both:
    :attr:`answer` holds the prose and :attr:`structured` the object matching
    the schema that was asked for. :attr:`raw` is kept out of ``repr`` and
    ``model_dump()``, as :attr:`BrowserSession.raw` is, because a task payload
    can carry the URL of the session the agent ran in. A backend's live-view
    URL stays there too: it grants control of the browser without a key, and is
    no use once the task has finished.
    """

    # Unknown keys are a provider bug: the untouched payloads belong in `raw`.
    model_config = ConfigDict(extra="forbid")

    provider: str
    task: str
    answer: str | None = Field(default=None, description="The prose answer, when the task produced one.")
    structured: dict[str, Any] | list[Any] | None = Field(
        default=None,
        description="The structured answer, conforming to the schema that was asked for.",
    )
    steps: list[BrowseStep] = Field(default_factory=list, description="The actions the backend reported, in order.")
    outcome: BrowseOutcome = Field(
        default="complete",
        description="Whether the backend finished the task or said it stopped early.",
    )
    request_id: str | None = Field(default=None, description="The backend's own id for the task.")
    recording_url: str | None = Field(
        default=None,
        description="A recording of the task, when the backend returned one.",
    )
    raw: list[dict[str, Any]] | None = Field(
        default=None,
        repr=False,
        exclude=True,
        description=(
            "The provider's untouched payloads, one per call the task took. Read it as an attribute; "
            "it is never serialized."
        ),
    )
    decision: DecisionRecord | None = Field(default=None, description=DECISION_DESCRIPTION)


class BrowseProgress(BaseModel):
    """Where one browse task stands, and its answer once it has one."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(description="The backend's id for this task, which is what a later poll is keyed on.")
    state: JobState
    response: BrowseResponse | None = Field(
        default=None,
        description="The answer. Set exactly when `state` is 'completed'.",
    )
    detail: str | None = Field(
        default=None,
        description="Whatever the backend said about this state -- an error message, a progress note.",
    )

    @property
    def is_terminal(self) -> bool:
        """Whether this task has stopped, one way or another."""
        return self.state not in ("queued", "running")
