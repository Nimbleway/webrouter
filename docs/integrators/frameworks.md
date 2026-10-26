# Framework Integration

web-router lets frameworks integrate once against six methods (`search`, `extract`, `run_template`, `research`, `open_browser`, `browse`), letting their users switch providers at runtime rather than at integration time.

## The integration pattern

Frameworks integrate against web-router's normalized interface, not individual providers:

```python
# In your framework
from web_router import WebRouter


class YourFramework:
    def __init__(self, search_provider="auto"):
        self.router = WebRouter(search_provider)

    def search(self, query: str):
        response = self.router.search(query, max_results=5)
        return self.process_results(response.results)
```

Your users then control which backend serves their requests:

```python
# User code
framework = YourFramework(search_provider="nimble")  # or "tavily", "brave", etc.
results = framework.search("query")
```

## Benefits

### For frameworks

- **One integration**: works with 14 search backends (DeepSeek included) and 11 extract backends (DeepSeek included; `max` races that chain), a Nimble template catalogue, 5 research backends, 6 browser-session backends (Kernel included) and 3 browse backends
- **Provider-agnostic**: no conditional logic for each backend
- **Consistent errors**: all providers raise the same exception hierarchy
- **Testing flexibility**: mock the router or register fake providers for tests

### For users

- **Runtime switching**: change providers without changing code
- **Credentials from environment**: `NIMBLE_API_KEY`, `TAVILY_API_KEY`, etc.
- **Fallback support**: the `auto` provider tries credentialed backends, then keyless ones
- **Escape hatch**: `provider_options` reaches provider-specific features

## Integration checklist

When integrating web-router:

- [ ] Accept a `search_provider` or `extract_provider` parameter
- [ ] Default to `"auto"` (web-router's default provider)
- [ ] Pass it to `WebRouter(search_provider)` or `AsyncWebRouter(...)`
- [ ] Use `router.search()` or `router.extract()` in your implementation
- [ ] Let `WebRouterError` propagate or wrap it in your own exception
- [ ] Document credential environment variables (`NIMBLE_API_KEY`, etc.)
- [ ] Document that `auto` tries credentialed backends, then keyless ones, and that every key a user holds is used, so a key held only for research also serves (and bills) plain searches. Users who want a different order pass `provider_config={"auto": {"chain": ...}}`.

## Example: RAG pipeline

```python
from web_router import AsyncWebRouter, WebRouterError


class RAGPipeline:
    def __init__(self, search_provider="auto", extract_provider="auto"):
        self.search_provider = search_provider
        self.extract_provider = extract_provider
        self.router = AsyncWebRouter(search_provider)

    async def retrieve_context(self, query: str, k: int = 3):
        try:
            # Search for relevant URLs
            search_response = await self.router.search(query, max_results=k)

            # Extract page contents
            extract_response = await self.router.extract(
                search_response.urls, format="markdown", provider=self.extract_provider
            )

            return [doc.content for doc in extract_response.documents]

        except WebRouterError as e:
            # Wrap or propagate
            raise
```

## Credential management

Credentials come from the environment or explicit configuration:

```python
# Option 1: Environment variables (recommended)
# User sets NIMBLE_API_KEY, TAVILY_API_KEY, etc.
framework = YourFramework(search_provider="nimble")


# Option 2: Explicit config (useful for multi-tenant)
class YourFramework:
    def __init__(self, search_provider="auto", provider_config=None):
        self.router = WebRouter(search_provider, provider_config=provider_config)


framework = YourFramework("nimble", provider_config={"nimble": {"api_key": get_credential("nimble")}})
```

`provider_config` reaches a backend whether the user names it or `auto` reaches it, and a backend given an `api_key` there joins `auto`'s default chain the way an environment key does.

If your users already hold an authenticated Nimble client (one per end user, say), pass it as `async_client` instead of a key. This works on `AsyncWebRouter` only: a synchronous `WebRouter` handed a client raises `ConfigurationError`.

```python
from nimble_python import AsyncNimble
from web_router import AsyncWebRouter

client = AsyncNimble(api_key=user_key)
router = AsyncWebRouter(provider_config={"nimble": {"async_client": client}})
# auto counts Nimble as configured; the client stays yours and is not closed by the router
```

Do not require your users to manage credentials in multiple places. Let web-router handle it.

## Provider-specific options

Expose `provider_options` as a pass-through for advanced users:

```python
def search(self, query: str, **provider_options):
    response = self.router.search(query, provider_options=provider_options)
    return self.process_results(response.results)
```

Document that these are provider-specific and may not work across backends.

## Testing

Use fake providers for tests:

```python
from web_router import register_provider
from web_router.providers import SearchProvider
from web_router.types import SearchRequest, SearchResponse, SearchResult


class FakeSearchProvider(SearchProvider):
    name = "fake"
    supported_parameters = frozenset({"max_results"})

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[SearchResult(url="https://example.com", title="Test")],
            raw={},
        )

    def _search(self, request: SearchRequest) -> SearchResponse:
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[SearchResult(url="https://example.com", title="Test")],
            raw={},
        )


register_provider("fake", FakeSearchProvider)

# In tests
framework = YourFramework(search_provider="fake")
```

## Reference integrations

web-router ships with ready-to-use integrations for major AI frameworks:

### LangChain

`web_router.integrations.langchain` provides `WebRouterSearch` and `WebRouterExtract` as LangChain tools:

```python
from web_router import WebRouter
from web_router.integrations.langchain import web_router_tools

tools = web_router_tools(WebRouter(), provider="nimble", max_results=5)
# [WebRouterSearch, WebRouterExtract], named web_search and web_extract, sharing one router
```

Pass `async_router=AsyncWebRouter()` as well to serve `agent.ainvoke` without a worker thread.

Install with the `langchain` extra: `uv sync --extra langchain` in your clone, or `pip install -e "/path/to/webrouter[langchain]"` from your own project.

### Claude (Anthropic)

`web_router.integrations.anthropic` provides `WebRouterTools` as Claude custom tools with local executor:

```python
from web_router import AsyncWebRouter
from web_router.integrations.anthropic import WebRouterTools

tools = WebRouterTools(AsyncWebRouter(), provider="nimble", max_results=5)
system_prompt = tools.guide()  # static guide: when to use which tool
definitions = tools.definitions()  # web_router_search, web_router_extract
# for each tool use: result = await tools.acall(name, input)
```

No SDK or extra needed: these are plain custom tool definitions for a Managed Agents agent or the Messages API, and `call` / `acall` run each call locally, on your keys.

### OpenAI

`web_router.integrations.openai` provides `WebRouterTools` as strict OpenAI function tools:

```python
from web_router import WebRouter
from web_router.integrations.openai import WebRouterTools

tools = WebRouterTools(WebRouter(), provider="nimble", max_results=5)
instructions = tools.guide()
definitions = tools.definitions()  # Responses API; chat_definitions() for Chat Completions
# for each function call: tools.call(call.name, call.arguments).function_call_output(call.call_id)
```

No SDK or extra needed. The definitions are strict, so OpenAI holds the model's arguments to the schema.

All three integrations follow the same rules: the model supplies only the query or the URLs, and the provider and options are fixed when the tools are built. The model reads a compact rendering of each result (text cut to `result_chars`, a page to `document_chars`), while your code keeps the full response. The guide is fixed text (`tools.guide()`, or `routing_guide()` from either module without building tools); there is no planner or per-query routing in the tools. The Claude and OpenAI modules share their tool logic in `integrations/_tools.py` and differ only in the envelope.

### MCP

For MCP clients (Claude Code, Claude Desktop, Cursor and others), the self-hosted stdio server `web-router-mcp` (`web_router.mcp.server:main`, also `python -m web_router.mcp`) exposes the methods as tools. It needs the `mcp` extra, plus `browser` for the `browser_*` tools; see [the MCP server guide](../mcp.md).

## Async support

Use `AsyncWebRouter` for async frameworks:

```python
from web_router import AsyncWebRouter


class AsyncFramework:
    def __init__(self, search_provider="auto"):
        self.router = AsyncWebRouter(search_provider)

    async def search(self, query: str):
        response = await self.router.search(query, max_results=5)
        return self.process_results(response.results)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.router.aclose()
```

Both routers expose the same interface. Prefer the async one for async frameworks to enable concurrent extract calls.

## Error handling

Wrap or propagate `WebRouterError` and its subclasses:

```python
from web_router import (
    WebRouterError,
    ConfigurationError,
    ProviderError,
    UnsupportedParameterError,
)

try:
    response = router.search(query)
except ConfigurationError as e:
    # Missing API key or configuration
    logging.error(f"Configuration error: {e}")
    raise
except UnsupportedParameterError as e:
    # Provider does not support a requested option
    logging.warning(f"Unsupported parameter: {e}")
    # Retry without the option, or switch providers
except ProviderError as e:
    # Backend failure (HTTP error, SDK exception, etc.)
    logging.error(f"Provider error: {e}")
    # Retry with another provider
except WebRouterError as e:
    # Catch-all for any other web-router error
    logging.error(f"Web router error: {e}")
    raise
```

All web-router exceptions derive from `WebRouterError`, so catching that covers the full error surface.

## See also

- [Provider contracts](../../contracts/): wire fields per provider. `make contracts` regenerates them.
- [Request and response models](../../src/web_router/types.py)
- [Adding a provider](../../CONTRIBUTING.md#adding-a-provider) and [promoting a provider option](../../CONTRIBUTING.md#promoting-a-provider-option)
- [Provider support](https://github.com/WebContextLayer/webrouter/blob/main/README.md#provider-support): which providers map which options
- [Error handling example](https://github.com/WebContextLayer/webrouter/blob/main/examples/error_handling.py): every exception and its fix
- [Routing example](https://github.com/WebContextLayer/webrouter/blob/main/examples/routing.py): provider configuration patterns
