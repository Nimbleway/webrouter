"""web-router: one interface over many web search, extraction, answer, research and browser APIs.

Point the router at a provider and call it; swapping backends is a one-word change rather than a rewrite.

    >>> from web_router import WebRouter
    >>> router = WebRouter("nimble")
    >>> response = router.search("what is retrieval augmented generation", max_results=5)
    >>> pages = router.extract(response.urls[:3], format="markdown")

:meth:`~web_router.WebRouter.live_data` runs a vendor's ready-made scraper for
one site -- Nimble's Extract Templates, Apify's Actors, Bright Data's scrapers,
Oxylabs' sources -- and returns structured records fetched for this call, with
:meth:`~web_router.WebRouter.live_data_sources` and
:meth:`~web_router.WebRouter.live_data_source` to find one and read its inputs:

    >>> records = router.live_data("books_pdp", {"url": "https://books.example/..."}, provider="nimble").records

:meth:`~web_router.WebRouter.answer` asks one question and returns the
backend's written answer with the sources it rests on, in a single call:

    >>> reply = router.answer("who won the 2022 world cup final", provider="linkup")

:meth:`~web_router.WebRouter.research` runs an agentic backend
over several turns and answers in minutes rather than milliseconds:

    >>> answer = router.research("how did nvidia's datacenter revenue mix shift in 2025")

:meth:`~web_router.WebRouter.browse` is the same shape with a hosted browser
agent doing the work -- Browser Use, Skyvern or Browserbase:

    >>> response = router.browse("find the top story", start_url="https://news.ycombinator.com")

And :meth:`~web_router.WebRouter.open_browser` hands back a browser session --
a cloud vendor's, or a Chromium on this machine through ``local`` -- to drive
over CDP or through the handle's own page actions, released when the block
holding it ends:

    >>> with router.open_browser() as browser:
    ...     browser.goto("https://example.com")
    ...     tree = browser.snapshot().tree
"""

from web_router import live_data_compat as _live_data_compat
from web_router._version import __version__
from web_router.client import AsyncWebRouter, WebRouter
from web_router.client_scope import AsyncBudgetScope, BudgetScope
from web_router.constants import DEFAULT_PROVIDER
from web_router.exceptions import (
    BenchmarkTimeoutError,
    BrowserActionError,
    BrowserActionTimeoutError,
    BrowseTimeoutError,
    ConfigurationError,
    DeadlineExceededError,
    ElementNotFoundError,
    MissingParameterError,
    NavigationError,
    NoAnswerProviderError,
    PageClosedError,
    ProviderError,
    ProviderNotFoundError,
    ProviderNotInstalledError,
    ResearchTimeoutError,
    RouterError,
    StaleElementError,
    UnsupportedFormatError,
    UnsupportedParameterError,
    WebRouterError,
)
from web_router.exceptions_budget import BudgetExceededError
from web_router.exceptions_live_data import LiveDataJobError, LiveDataSourceNotFoundError
from web_router.exceptions_typed import AllProvidersFailedError, InvalidParameterError
from web_router.exceptions_vendors import VendorNotFoundError, VendorWriteRefusedError
from web_router.live_data_search import NativeSourceSearch
from web_router.providers.answer import AnswerProvider
from web_router.providers.base import (
    AsyncBrowserHandle,
    BrowseJob,
    BrowseProvider,
    BrowserHandle,
    BrowserProvider,
    ExtractProvider,
    LiveDataProvider,
    ResearchJob,
    ResearchProvider,
    SearchProvider,
)
from web_router.registry import (
    available_browse_providers,
    available_browser_providers,
    available_extract_providers,
    available_live_data_providers,
    available_providers,
    available_research_providers,
    configured_browse_providers,
    configured_browser_providers,
    configured_extract_providers,
    configured_live_data_providers,
    configured_providers,
    configured_research_providers,
    register_browse_provider,
    register_browser_provider,
    register_extract_provider,
    register_live_data_provider,
    register_provider,
    register_research_provider,
    unregister_browse_provider,
    unregister_browser_provider,
    unregister_extract_provider,
    unregister_live_data_provider,
    unregister_provider,
    unregister_research_provider,
)
from web_router.registry_answer import (
    available_answer_providers,
    configured_answer_providers,
    register_answer_provider,
    unregister_answer_provider,
)
from web_router.routing import (
    Budget,
    Candidate,
    CompositeDecision,
    Plan,
    RouteDecision,
    RouteRequest,
    RouterLike,
    ToolName,
)
from web_router.routing.decider import Decider, DeciderQuestion, DeciderReply
from web_router.routing.decider_router import DeciderRouter
from web_router.routing.pool_status import BudgetStatus
from web_router.routing.registry import available_routers, register_router, unregister_router
from web_router.types import (
    BROWSE_PARAMETERS,
    BROWSER_PARAMETERS,
    EXTRACT_PARAMETERS,
    NORMALIZED_PARAMETERS,
    RESEARCH_PARAMETERS,
    BrowseOutcome,
    BrowseProgress,
    BrowseRequest,
    BrowseResponse,
    BrowserRequest,
    BrowserSession,
    BrowserState,
    BrowseStep,
    Citation,
    ExtractedDocument,
    ExtractFailure,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
    Provider,
    ResearchEffort,
    ResearchOutcome,
    ResearchProgress,
    ResearchRequest,
    ResearchResponse,
    ResearchState,
    SearchDepth,
    SearchRequest,
    SearchResponse,
    SearchResult,
    StealthLevel,
    Viewport,
)
from web_router.types_answer import ANSWER_PARAMETERS, AnswerRequest, AnswerResponse, Recency
from web_router.types_benchmark import BenchmarkReport, BenchmarkRun, ExtractBenchmarkReport, ExtractBenchmarkRun
from web_router.types_decision import DecisionAttempt, DecisionHop, DecisionMember, DecisionRecord
from web_router.types_live_data import (
    LIVE_DATA_PARAMETERS,
    LiveDataRequest,
    LiveDataResponse,
    LiveDataSample,
    LiveDataSource,
    LiveDataSourcePage,
)
from web_router.types_page import PageFormat, PageLoadState, PageSnapshot, PageState, Screenshot
from web_router.vendors import Vendor, VendorEndpoint, VendorResponse, VendorSummary

__all__ = [
    "ANSWER_PARAMETERS",
    "BROWSER_PARAMETERS",
    "BROWSE_PARAMETERS",
    "DEFAULT_PROVIDER",
    "EXTRACT_PARAMETERS",
    "LIVE_DATA_PARAMETERS",
    "NORMALIZED_PARAMETERS",
    "RESEARCH_PARAMETERS",
    "AllProvidersFailedError",
    "AnswerProvider",
    "AnswerRequest",
    "AnswerResponse",
    "AsyncBrowserHandle",
    "AsyncBudgetScope",
    "AsyncWebRouter",
    "BenchmarkReport",
    "BenchmarkRun",
    "BenchmarkTimeoutError",
    "BrowseJob",
    "BrowseOutcome",
    "BrowseProgress",
    "BrowseProvider",
    "BrowseRequest",
    "BrowseResponse",
    "BrowseStep",
    "BrowseTimeoutError",
    "BrowserActionError",
    "BrowserActionTimeoutError",
    "BrowserHandle",
    "BrowserProvider",
    "BrowserRequest",
    "BrowserSession",
    "BrowserState",
    "Budget",
    "BudgetExceededError",
    "BudgetScope",
    "BudgetStatus",
    "Candidate",
    "Citation",
    "CompositeDecision",
    "ConfigurationError",
    "DeadlineExceededError",
    "Decider",
    "DeciderQuestion",
    "DeciderReply",
    "DeciderRouter",
    "DecisionAttempt",
    "DecisionHop",
    "DecisionMember",
    "DecisionRecord",
    "ElementNotFoundError",
    "ExtractBenchmarkReport",
    "ExtractBenchmarkRun",
    "ExtractFailure",
    "ExtractFormat",
    "ExtractProvider",
    "ExtractRequest",
    "ExtractResponse",
    "ExtractedDocument",
    "InvalidParameterError",
    "LiveDataJobError",
    "LiveDataProvider",
    "LiveDataRequest",
    "LiveDataResponse",
    "LiveDataSample",
    "LiveDataSource",
    "LiveDataSourceNotFoundError",
    "LiveDataSourcePage",
    "MissingParameterError",
    "NativeSourceSearch",
    "NavigationError",
    "NoAnswerProviderError",
    "PageClosedError",
    "PageFormat",
    "PageLoadState",
    "PageSnapshot",
    "PageState",
    "Plan",
    "Provider",
    "ProviderError",
    "ProviderNotFoundError",
    "ProviderNotInstalledError",
    "Recency",
    "ResearchEffort",
    "ResearchJob",
    "ResearchOutcome",
    "ResearchProgress",
    "ResearchProvider",
    "ResearchRequest",
    "ResearchResponse",
    "ResearchState",
    "ResearchTimeoutError",
    "RouteDecision",
    "RouteRequest",
    "RouterError",
    "RouterLike",
    "Screenshot",
    "SearchDepth",
    "SearchProvider",
    "SearchRequest",
    "SearchResponse",
    "SearchResult",
    "StaleElementError",
    "StealthLevel",
    "ToolName",
    "UnsupportedFormatError",
    "UnsupportedParameterError",
    "Vendor",
    "VendorEndpoint",
    "VendorNotFoundError",
    "VendorResponse",
    "VendorSummary",
    "VendorWriteRefusedError",
    "Viewport",
    "WebRouter",
    "WebRouterError",
    "__version__",
    "available_answer_providers",
    "available_browse_providers",
    "available_browser_providers",
    "available_extract_providers",
    "available_live_data_providers",
    "available_providers",
    "available_research_providers",
    "available_routers",
    "configured_answer_providers",
    "configured_browse_providers",
    "configured_browser_providers",
    "configured_extract_providers",
    "configured_live_data_providers",
    "configured_providers",
    "configured_research_providers",
    "register_answer_provider",
    "register_browse_provider",
    "register_browser_provider",
    "register_extract_provider",
    "register_live_data_provider",
    "register_provider",
    "register_research_provider",
    "register_router",
    "unregister_answer_provider",
    "unregister_browse_provider",
    "unregister_browser_provider",
    "unregister_extract_provider",
    "unregister_live_data_provider",
    "unregister_provider",
    "unregister_research_provider",
    "unregister_router",
]


_live_data_compat.install()


def __getattr__(name: str) -> object:
    """The names live data had as "templates" -- ``TemplateResponse`` and the rest -- with a warning."""
    from web_router.live_data_compat import resolve

    return resolve(name)
