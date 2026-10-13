"""web-router: one interface over many web search, extraction, research and browser APIs.

Point the router at a provider and call it; swapping backends is a one-word
change rather than a rewrite.

    >>> from web_router import WebRouter
    >>> router = WebRouter("nimble")
    >>> response = router.search("what is retrieval augmented generation", max_results=5)
    >>> pages = router.extract(response.urls[:3], format="markdown")

A third method, :meth:`~web_router.WebRouter.research`, runs an agentic backend
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

from web_router._version import __version__
from web_router.client import AsyncWebRouter, WebRouter
from web_router.constants import DEFAULT_PROVIDER
from web_router.exceptions import (
    BenchmarkTimeoutError,
    BrowserActionError,
    BrowserActionTimeoutError,
    BrowseTimeoutError,
    ConfigurationError,
    ElementNotFoundError,
    MissingParameterError,
    NavigationError,
    PageClosedError,
    ProviderError,
    ProviderNotFoundError,
    ProviderNotInstalledError,
    ResearchTimeoutError,
    StaleElementError,
    UnsupportedFormatError,
    UnsupportedParameterError,
    WebRouterError,
)
from web_router.providers.base import (
    AsyncBrowserHandle,
    BrowseJob,
    BrowseProvider,
    BrowserHandle,
    BrowserProvider,
    ExtractProvider,
    ResearchJob,
    ResearchProvider,
    SearchProvider,
)
from web_router.registry import (
    available_browse_providers,
    available_browser_providers,
    available_extract_providers,
    available_providers,
    available_research_providers,
    configured_browse_providers,
    configured_browser_providers,
    configured_extract_providers,
    configured_providers,
    configured_research_providers,
    register_browse_provider,
    register_browser_provider,
    register_extract_provider,
    register_provider,
    register_research_provider,
    unregister_browse_provider,
    unregister_browser_provider,
    unregister_extract_provider,
    unregister_provider,
    unregister_research_provider,
)
from web_router.types import (
    BROWSE_PARAMETERS,
    BROWSER_PARAMETERS,
    EXTRACT_PARAMETERS,
    NORMALIZED_PARAMETERS,
    RESEARCH_PARAMETERS,
    BenchmarkReport,
    BenchmarkRun,
    BrowseOutcome,
    BrowseProgress,
    BrowseRequest,
    BrowseResponse,
    BrowserRequest,
    BrowserSession,
    BrowserState,
    BrowseStep,
    Citation,
    ExtractBenchmarkReport,
    ExtractBenchmarkRun,
    ExtractedDocument,
    ExtractFailure,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
    PageFormat,
    PageLoadState,
    PageSnapshot,
    PageState,
    Provider,
    ResearchEffort,
    ResearchOutcome,
    ResearchProgress,
    ResearchRequest,
    ResearchResponse,
    ResearchState,
    Screenshot,
    SearchDepth,
    SearchRequest,
    SearchResponse,
    SearchResult,
    StealthLevel,
    Viewport,
)

__all__ = [
    "BROWSER_PARAMETERS",
    "BROWSE_PARAMETERS",
    "DEFAULT_PROVIDER",
    "EXTRACT_PARAMETERS",
    "NORMALIZED_PARAMETERS",
    "RESEARCH_PARAMETERS",
    "AsyncBrowserHandle",
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
    "Citation",
    "ConfigurationError",
    "ElementNotFoundError",
    "ExtractBenchmarkReport",
    "ExtractBenchmarkRun",
    "ExtractFailure",
    "ExtractFormat",
    "ExtractProvider",
    "ExtractRequest",
    "ExtractResponse",
    "ExtractedDocument",
    "MissingParameterError",
    "NavigationError",
    "PageClosedError",
    "PageFormat",
    "PageLoadState",
    "PageSnapshot",
    "PageState",
    "Provider",
    "ProviderError",
    "ProviderNotFoundError",
    "ProviderNotInstalledError",
    "ResearchEffort",
    "ResearchJob",
    "ResearchOutcome",
    "ResearchProgress",
    "ResearchProvider",
    "ResearchRequest",
    "ResearchResponse",
    "ResearchState",
    "ResearchTimeoutError",
    "Screenshot",
    "SearchDepth",
    "SearchProvider",
    "SearchRequest",
    "SearchResponse",
    "SearchResult",
    "StaleElementError",
    "StealthLevel",
    "UnsupportedFormatError",
    "UnsupportedParameterError",
    "Viewport",
    "WebRouter",
    "WebRouterError",
    "__version__",
    "available_browse_providers",
    "available_browser_providers",
    "available_extract_providers",
    "available_providers",
    "available_research_providers",
    "configured_browse_providers",
    "configured_browser_providers",
    "configured_extract_providers",
    "configured_providers",
    "configured_research_providers",
    "register_browse_provider",
    "register_browser_provider",
    "register_extract_provider",
    "register_provider",
    "register_research_provider",
    "unregister_browse_provider",
    "unregister_browser_provider",
    "unregister_extract_provider",
    "unregister_provider",
    "unregister_research_provider",
]
