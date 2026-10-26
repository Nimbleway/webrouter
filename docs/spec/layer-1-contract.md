# Layer 1 Contract: web-router

**Status:** Working draft, updated for package **0.7.0** (`src/web_router/_version.py`, `main` at `10cddcd`). Earlier verification checks ran against 0.0.1, before this repository moved, and have **not** been re-run on 0.7.0. See [Status and verification](#status-and-verification).

This document defines the Layer 1 working contract of web-router: one interface that applications use to reach many web API backends. Layer 1 is the **interface contract**: one request and response shape per capability, a fixed-order `auto` fallback chain, and a Python library that implements both. It is an internal draft toward a multi-provider layer. Layer 2 (task routing, budget-aware selection, quality optimization) is out of scope here.

## 1. Purpose and Non-Goals

### What web-router Layer 1 is for

web-router Layer 1 solves the **provider integration problem** for web capabilities: search, extraction, research and browser automation. Each backend has its own SDK, parameter names and response shape. "How many results" is `max_results` on Tavily, `count` on Brave and You.com, `numResults` on Exa. The page text is `content` on Tavily, `text` on Exa, `snippet` on Perplexity. Swapping backends means rewriting the call, the options and the parsing.

Layer 1 unifies this:

1. **One request shape per capability.** `search(query, max_results=5)` works the same way whichever backend answers.
2. **One response shape per capability.** `result.content` is the page body, `result.url` the source URL, `result.score` the relevance (when the backend gives one), regardless of backend.
3. **Fail-loud parameter handling.** A backend that cannot honor an option, such as `country="US"` on Brave, raises `UnsupportedParameterError` rather than silently dropping it. A search that ignores your constraint is worse than one that refuses to run.
4. **Backend fallback.** The `auto` provider tries backends in a fixed order, then keyless tiers where they exist.
5. **Zero data invention.** If a backend has no relevance score, `score` stays `None`. No estimation, no synthesis.
6. **Provenance.** `response.provider` names who actually answered; `response.raw` keeps the untouched backend payload.

### What it is not

- **Not a task router by default.** Layer 1 routes by name (`provider="tavily"`) or by the `auto` chain's fixed order, not by task. Passing `decider=` opts in to `classify()` and `execute()` (Smart Routing v0), which pick a method and an auto chain before the call. Without `decider=`, that default is unchanged.
- **Not a prompt router.** No prompt rewriting and no quality model in the default path. The caller decides what to ask, unless `decider=` is set.
- **Not budget-aware.** Layer 1 does not decide "cheap is good enough here."
- **Not a hosted service.** web-router is a library. Credentials come from the caller's environment or code; each key goes only to the backend it belongs to. The MCP server is self-hosted: it runs as a stdio subprocess of the client.
- **Not a LiteLLM replacement.** LiteLLM unifies model APIs; web-router unifies web search, extraction, research and browser automation. They solve adjacent problems.

### Adopter story

An application that uses Tavily search today writes Tavily-shaped calls. Adding Nimble as a fallback means learning Nimble's API, duplicating the call logic, and mapping two response shapes. A third provider means doing it again.

With web-router, switching or adding a provider is one argument:

```python
# Before: Tavily-specific
import os

from tavily import TavilyClient

client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
response = client.search(query="...", max_results=5, search_depth="advanced")
results = [{"url": r["url"], "content": r["content"]} for r in response["results"]]

# After: provider-agnostic
from web_router import WebRouter, Provider

router = WebRouter(Provider.TAVILY)
response = router.search("...", max_results=5, search_depth="deep")
results = [{"url": r.url, "content": r.content} for r in response.results]

# Fallback: the default provider is auto
router = WebRouter()  # every backend you hold a key for, in a fixed order, then the keyless tiers
```

The same pattern applies to extraction (page content for a URL you already have), research (an agentic run that searches, reads and cross-checks over many turns), and browser automation (opening a session and driving it, or handing a task to a hosted agent).

## 2. Capability Set

web-router Layer 1 defines six capabilities. Each has its own registry and resolves a provider name against its own list, so a backend that searches may not extract, and vice versa. Asking a capability for a backend it does not serve raises, naming the backends it does serve.

### `search`: ranked links with page text

A query goes in; a ranked list of URLs with titles and page text comes out. Latency is typically under a second.

Built-in backends: `nimble`, `tavily`, `you`, `brave`, `exa`, `parallel`, `perplexity`, `linkup`, `webiq`, `keenable` (with a key, or keyless on its rate-limited `/public` tier), `deepseek`, plus three keyless hosted MCP endpoints: `you-mcp`, `exa-mcp`, `parallel-mcp`.

```python
response = router.search("what is retrieval augmented generation", max_results=5)
for result in response.results:
    print(result.title, result.url, result.content[:100])
```

### `extract`: page contents for URLs you already have

Read pages you already have URLs for, as markdown, HTML or text. Latency is typically under a second.

Built-in backends: `nimble`, `tavily`, `exa` (text only), `linkup`, `firecrawl`, `oxylabs`, `brightdata`, `webiq` (pages in WebIQ's own index), `keenable` (markdown only; with a key, or keyless on its `/public` tier), `deepseek` (markdown only), plus `jina` (keyless) and `max`. `max` is not a fetch backend of its own: it races the members `auto` would try and returns the first document, it is never the default, and `response.provider` names the member that won. With no `format`, every backend that can return markdown returns markdown; one that cannot (Exa) returns its own default, and `ExtractedDocument.format` names what came back. `auto` accepts every format and routes each request to a member that can return it.

```python
pages = router.extract(["https://example.com", "https://example.org"], format="markdown")
for doc in pages.documents:
    print(doc.url, len(doc.content))
```

### `run_template`: a vendor's saved extraction

Run one of a vendor's saved, site-specific extractions on one page and get the record it parsed. The inputs are the template's own (a URL, an id, a slug, or a search term), which is why this is its own method rather than an option on `extract`. `provider` is required: a template name belongs to one vendor, and there is no template `auto`.

Built-in backends: `nimble` (Nimble's Extract Templates). `templates(provider=...)` lists one page of that vendor's catalogue, and `template(name, provider=...)` describes one, including the JSON Schema of its inputs and of the record it returns. `format` asks for the page beside the record (`markdown` or `html`); unset, the record comes back alone. A run the backend could not complete raises `ProviderError`, and an unknown name raises `TemplateNotFoundError`.

```python
record = router.run_template("some-template", {"url": "https://example.com"}, provider="nimble")
print(record.data)
```

### `research`: an agentic run over multiple sources

The backend searches, reads and cross-checks over many turns, then returns a prose or structured answer with citations. Latency is **minutes**, and **every backend bills for the run**.

Built-in backends: `nimble`, `parallel`, `perplexity`, `linkup`, `you`. (Exa retired its Research API upstream; web-router no longer ships an Exa research provider. Exa search and extract are unaffected.) `auto` has no keyless tier, because every backend bills.

```python
response = router.research("who founded the Wikimedia Foundation", effort="medium")
print(response.answer, response.citations)
```

`submit_research` returns a job instead of blocking:

```python
job = router.submit_research("who founded the Wikimedia Foundation", effort="medium")
# do other work
progress = job.status()  # one poll: state is queued, running, completed, failed or cancelled
response = job.result()  # blocks until the run finishes, or raises ResearchTimeoutError
```

### `open_browser`: a browser session to drive yourself

Open a browser session and get a handle. Drive it over its CDP URL with Playwright, browser-use or Stagehand, or through the handle's own page actions: `goto`, `snapshot`, `click`, `type`, `press`, `screenshot`, `read` (these need the `browser` extra, which is Playwright). Opening takes a few seconds; **a cloud backend bills until the session is released**.

Built-in backends: `steel`, `browserbase`, `hyperbrowser`, `anchor`, `kernel`, plus `local` (keyless; launches Chrome or Chromium on this machine). `auto` tries the vendors you hold keys for, then `local`.

```python
with router.open_browser() as browser:
    browser.goto("https://news.ycombinator.com")
    snapshot = browser.snapshot()
    print(snapshot.tree)
    browser.click(ref="e14")
    screenshot = browser.screenshot()
```

The session is released when the `with` block ends, on every path, and `router.close()` releases any the caller lost track of.

### `browse`: a hosted browser-agent task

Hand a task to a hosted browser agent: the task goes in, the vendor's agent drives one of its own browsers through it, and an answer comes out minutes later. Same shape as `research`, with a browser agent in place of a search index. Latency is **minutes**, and **every backend bills for the task**.

Built-in backends: `browser-use`, `skyvern`, `browserbase`. `auto` has no keyless tier.

```python
response = router.browse("Find the top story on Hacker News and summarize it")
print(response.answer, response.steps)
```

`submit_browse` returns a job that works like the research job (`status()`, `result()`, `cancel()`):

```python
job = router.submit_browse("Find the top story on Hacker News")
response = job.result()
```

### Measurement helpers (not selection)

Three methods measure backends; none of them chooses one for you:

- **`benchmark(query, providers=...)`** runs the same search through several providers at once and returns a `BenchmarkReport`: one `BenchmarkRun` per provider with latency, result count, and the response or the error. Measured facts only: no quality score, no price.
- **`benchmark_extract(urls, providers=...)`** fetches the same URLs through several extract providers at once and returns an `ExtractBenchmarkReport`: one `ExtractBenchmarkRun` per provider with latency and the response or the error. Measured facts only, same rule as `benchmark()`.
- **`evaluate(dataset, providers=..., judge=...)`** runs every question in a `Dataset` through several providers and has an LLM judge grade a synthesized answer against the gold answer (SimpleQA grader). Question sets load with `Dataset.for_set(...)` (`simpleqa`, `simpleqa-verified`, `frames`, `sealqa-0`, `sealqa-hard`, or your own `csv`). Needs the `evals` extra.

They exist so routing decisions can be made from data. None of them feeds back into `auto`'s order at runtime: the order changes only through a PR backed by a provider scan.

## 3. Unified Request Shape

Every capability has one request model. A provider is called with the fields it declares support for, and nothing else. Fields left as `None` are not sent, so each backend applies its own defaults rather than inheriting ours.

### Common pattern

Every request model is a `pydantic.BaseModel` with `extra="forbid`, so unknown fields raise at validation. Every optional parameter defaults to `None` and is omitted from the provider call when unset. Every request carries `provider_options` (an untranslated passthrough) and exposes `normalized_parameters()` and `specified_parameters()` for capability negotiation.

Requests are normally built for you by the router methods (`router.search(...)` and so on); the models are exported for providers and tests.

### `SearchRequest`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `query` | `str` | ✓ | - | The search query (minimum 1 character) |
| `max_results` | `int \| None` | | `None` | Upper bound on results returned (≥ 1) |
| `search_depth` | `"lite" \| "fast" \| "deep" \| None` | | `None` | How much page content to retrieve |
| `include_domains` | `list[str] \| None` | | `None` | Restrict results to these domains |
| `exclude_domains` | `list[str] \| None` | | `None` | Drop results from these domains |
| `country` | `str \| None` | | `None` | Country code for geo-targeting, e.g. `"US"` |
| `locale` | `str \| None` | | `None` | Language code, e.g. `"en"` |
| `provider_options` | `dict[str, Any]` | | `{}` | Passed straight through to the provider, untranslated |

**`search_depth` semantics:**
- `"lite"`: title, URL and description only. Cheapest, best at high volume.
- `"fast"`: a truncated page body, enough for most agent reasoning.
- `"deep"`: full page content. Slowest and most expensive.

Providers map these levels onto their own vocabulary (for example Tavily's `"basic"` / `"advanced"`). Most backends map only some options: Nimble maps all of them, Linkup adds the domain filters, and several map only `max_results`.

### `ExtractRequest`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `urls` | `list[str]` | ✓ | - | URLs to fetch, in the order documents come back (minimum 1) |
| `format` | `"markdown" \| "html" \| "text" \| None` | | `None` | Which representation of each page to return |
| `provider_options` | `dict[str, Any]` | | `{}` | Passed straight through to the provider, untranslated |

**Format semantics:**
- `"markdown"`: page as markdown. Structure kept, markup dropped.
- `"html"`: the page's HTML as the backend fetched it.
- `"text"`: visible text with no markup.

A backend that cannot return the requested format raises `UnsupportedFormatError`. With `format=None`, the call asks for `markdown` from every backend that can return it, and the backend's own default from one that cannot. `ExtractedDocument.format` names what came back.

### `ResearchRequest`

```python
response = router.research(
    "who founded the Wikimedia Foundation",
    effort="medium",
    output_schema=CompanyInfo,  # a pydantic model or a JSON Schema dict
)
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `query` | `str` | ✓ | - | The question, or the instructions for the run |
| `effort` | `"low" \| "medium" \| "high" \| "max" \| None` | | `None` | How much work the run does |
| `output_schema` | `dict[str, Any] \| None` | | `None` | JSON Schema the answer must conform to. The router methods also accept a pydantic model and convert it. |
| `records` | `list[dict[str, Any]] \| None` | | `None` | Partial records to enrich, each shaped like `output_schema` |
| `include_domains` | `list[str] \| None` | | `None` | Restrict sources to these domains |
| `exclude_domains` | `list[str] \| None` | | `None` | Keep these domains out of the sources |
| `provider_options` | `dict[str, Any]` | | `{}` | Passed straight through to the provider, untranslated |

**Notes:**
- `output_schema` is mapped by `nimble`, `parallel` and `linkup`. `perplexity` and `you` do not map it yet and raise `UnsupportedParameterError`.
- `include_domains`, `exclude_domains` and `records` are mapped by `nimble` only. Others raise `UnsupportedParameterError`.
- Backends with fewer effort tiers map more than one level onto the same setting rather than inventing a difference.
- `research()` takes a `timeout` (seconds to wait). Running out raises `ResearchTimeoutError` and leaves the run going; it does not cancel it.

### `BrowserRequest`

```python
with router.open_browser(timeout=300, viewport={"width": 1280, "height": 720}, stealth="low") as browser:
    ...
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `proxy_country` | `str \| None` | | `None` | ISO 3166-1 alpha-2 country the traffic exits from |
| `stealth` | `"off" \| "low" \| "high" \| None` | | `None` | Anti-detection level |
| `solve_captchas` | `bool \| None` | | `None` | Ask the backend to solve CAPTCHAs it meets |
| `profile` | `str \| None` | | `None` | The backend's id for a saved profile to start from |
| `persist_profile` | `bool \| None` | | `None` | Save cookies and storage back to `profile` when the session ends |
| `timeout` | `float \| None` | | `None` | Seconds the session may live |
| `idle_timeout` | `float \| None` | | `None` | Seconds the session may sit unused before the backend ends it |
| `viewport` | `Viewport \| None` | | `None` | `{"width": int, "height": int}` |
| `headless` | `bool \| None` | | `None` | Run without a visible window |
| `provider_options` | `dict[str, Any]` | | `{}` | Passed straight through to the provider, untranslated |

The provider is chosen by the `open_browser(provider=...)` argument, not by a request field.

**Notes:**
- A backend refuses an option it does not map, or a value it cannot serve (for example, Browserbase maps `stealth` as `low` / `high` only, so `stealth="off"` raises).
- `profile` is one backend's saved login. `persist_profile` without a `profile` raises. `auto` refuses both, because a profile belongs to one backend.
- `timeout` is the one field filled in when unset: a backend that maps a lifetime limit is sent 600 seconds (`BROWSER_TIMEOUT`) rather than its own default, which on some is hours, because a session nobody released bills until the backend ends it. Pass a larger `timeout` explicitly to go past it. Kernel maps no lifetime, so `timeout` raises and no default lifetime is sent. Kernel does map `idle_timeout`, `stealth`, `headless`, `viewport`, `profile` and `persist_profile`.

### `BrowseRequest`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `task` | `str` | ✓ | - | What the agent should do, in plain words (minimum 1 character) |
| `start_url` | `str \| None` | | `None` | The page the agent starts on |
| `output_schema` | `dict[str, Any] \| None` | | `None` | JSON Schema the answer must conform to (the router methods also accept a pydantic model) |
| `max_steps` | `int \| None` | | `None` | Upper bound on the actions the agent may take (≥ 1) |
| `allowed_domains` | `list[str] \| None` | | `None` | Keep the agent on these domains (an empty list is refused) |
| `provider_options` | `dict[str, Any]` | | `{}` | Passed straight through to the provider, untranslated |

**Notes:**
- There is no `effort` or `model` field: the agent behind one vendor is not comparable to another's, so those go through `provider_options`.
- `browser-use` maps `start_url`, `output_schema`, `max_steps` and `allowed_domains`. Skyvern maps `start_url` only. Browserbase's browse provider maps none of these, so `start_url` raises `UnsupportedParameterError` before billing.
- A backend that cannot start without an option lists it in `required_parameters`, and the base class raises `MissingParameterError` before anything is billed. A backend that cannot take an option raises `UnsupportedParameterError`, also before billing.
- `browse()` takes a `timeout`. Running out raises `BrowseTimeoutError` and leaves the task going.

## 4. Unified Response Shape

Every capability returns a normalized response, plus the backend's untouched payload in `raw`. Unmapped provider fields stay in each item's `extra`. Normalization is never lossy.

### Common pattern

Every response model is a `pydantic.BaseModel` with `extra="forbid"`. Every response carries:
- `provider: str`: which backend actually answered (never `"auto"`)
- `raw`: the untouched backend payload (a dict for search and browser sessions; a list of payloads, one per API call, for extract, research and browse)

### `SearchResponse`

```python
response = router.search("...")
print(response.provider)  # "tavily", "nimble", ...
print(len(response))  # number of results
print(response.urls)  # result URLs in rank order
```

| Field | Type | Description |
|-------|------|-------------|
| `provider` | `str` | Which backend actually answered |
| `query` | `str` | The query that was sent |
| `results` | `list[SearchResult]` | The ranked results |
| `answer` | `str \| None` | A synthesized answer, when the provider returned one |
| `request_id` | `str \| None` | The provider's request id, when given |
| `raw` | `dict[str, Any] \| None` | The untouched backend payload |

**`SearchResult` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `url` | `str` | The result URL |
| `title` | `str` | The result title (empty string if missing) |
| `description` | `str` | A snippet-length summary |
| `content` | `str` | The fullest text available for this result |
| `score` | `float \| None` | Relevance score, when the provider gives one |
| `published_date` | `str \| None` | Publication date as the provider reported it |
| `extra` | `dict[str, Any]` | Unmapped provider fields |

**Field selection rules:**
- `description`: the provider's own snippet when it gives one, otherwise the head of the page body
- `content`: the page body when the provider returned one, otherwise the description
- `score`: `None` when the backend has none; never synthesized
- `published_date`: `None` when unavailable; never estimated

### `ExtractResponse`

```python
response = router.extract([...], format="markdown")
for doc in response.documents:
    print(doc.url, doc.format, len(doc.content))
print(response.failures)  # URLs that failed, with the reason
```

| Field | Type | Description |
|-------|------|-------------|
| `provider` | `str` | Which backend actually answered |
| `documents` | `list[ExtractedDocument]` | The extracted pages |
| `failures` | `list[ExtractFailure]` | URLs that failed, with the reason |
| `request_id` | `str \| None` | The provider's request id (the first, for one-URL-per-call backends) |
| `raw` | `list[dict[str, Any]] \| None` | Untouched payloads, one per API call |

`response.urls` lists the document URLs in the order they were asked for.

**`ExtractedDocument` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `url` | `str` | The page URL |
| `title` | `str` | The page title (empty string if missing) |
| `content` | `str` | The page body, in the representation `format` names |
| `format` | `"markdown" \| "html" \| "text" \| None` | Which representation `content` holds |
| `status_code` | `int \| None` | The fetched page's HTTP status, when reported |
| `published_date` | `str \| None` | Publication date, when reported |
| `extra` | `dict[str, Any]` | Unmapped provider fields |

**`ExtractFailure` fields:** `url: str`, `error: str`.

**Partial success semantics:**
- If the API answers 200 but reports a refused fetch in a status field, that URL is an `ExtractFailure`, not a document with empty content.
- A call that partly fails stays partly successful: `failures` records what went wrong, and the call raises only when **nothing came back at all**.

### `ResearchResponse`

```python
response = router.research("...", output_schema=MySchema)
print(response.answer)  # prose, when the run produced it
print(response.structured)  # the object, when output_schema was given
print(response.outcome)  # "complete" or "partial"
for citation in response.citations:
    print(citation.url, citation.title)
```

| Field | Type | Description |
|-------|------|-------------|
| `provider` | `str` | Which backend actually answered |
| `query` | `str` | The question that was sent |
| `answer` | `str \| None` | The prose answer, when the run produced one |
| `structured` | `dict[str, Any] \| list[Any] \| None` | The structured answer, conforming to the requested schema |
| `citations` | `list[Citation]` | Sources behind the answer |
| `outcome` | `"complete" \| "partial"` | Whether the run finished or the backend said it stopped early |
| `request_id` | `str \| None` | The backend's id for the run |
| `raw` | `list[dict[str, Any]] \| None` | Untouched payloads, one per call the run took |

**`Citation` fields:** `url`, `title`, `snippet` (the quoted passage, when given), `field` (which part of the answer it backs, when the backend attributes per field), `extra`.

**`outcome` semantics:**
- `"partial"` **only when the API itself reports it**: an exhausted budget, a truncated generation.
- A run that simply found little is `"complete"`. The router never infers `partial`.

Job progress (`ResearchProgress`, from `job.status()`) carries `job_id`, `state` (`queued`, `running`, `completed`, `failed`, `cancelled`), `response` (set exactly when `completed`) and `detail`.

### `BrowserSession`

```python
with router.open_browser() as handle:
    session = handle.session
    print(session.session_id, session.provider, handle.status())
    cdp_url: str = handle.cdp_url  # plain string when read from the handle
```

| Field | Type | Description |
|-------|------|-------------|
| `provider` | `str` | Which backend opened the session |
| `session_id` | `str` | The backend's id for the session |
| `cdp_url` | `SecretStr` | The CDP endpoint. A credential: printed as asterisks |
| `control_url` | `SecretStr \| None` | A page where a person can see and drive the session, when the backend returns one. A credential, like `cdp_url` |
| `live_view_url` | `str \| None` | A page that shows the session live and grants no control, when offered |
| `expires_at` | `datetime \| None` | When the backend will end the session, when it says |
| `raw` | `dict[str, Any] \| None` | Untouched payload; kept out of `repr()` and `model_dump()` |

Session state comes from `handle.status()`: `starting`, `running`, `released`, `expired` or `failed`.

**Credential handling:**
- The CDP URL is a credential: whoever holds it drives the browser, and some backends put the account key in it.
- `control_url` is a credential for the same reason. `handle.control_url()` returns the plain string, and asks a backend that hands the page out only on request. Use it to log in on a saved `profile`.
- Both are `SecretStr`s, and `raw` (which may repeat them) is never serialized.
- `handle.cdp_url` returns the plain string, because that is the one moment it is needed. `session.cdp_url.get_secret_value()` does the same. `live_view_url` stays a plain string, for a page that grants no control.

### `BrowseResponse`

```python
response = router.browse("...")
print(response.answer)
print(response.structured)  # when output_schema was given
print(response.outcome)  # "complete" or "partial"
for step in response.steps:
    print(step.action, step.url, step.detail)
```

| Field | Type | Description |
|-------|------|-------------|
| `provider` | `str` | Which backend actually answered |
| `task` | `str` | The task that was sent |
| `answer` | `str \| None` | The agent's prose answer |
| `structured` | `dict[str, Any] \| list[Any] \| None` | The structured answer, when a schema was requested |
| `steps` | `list[BrowseStep]` | The actions the backend reported, in order (empty when it reports none) |
| `outcome` | `"complete" \| "partial"` | Whether the task finished or the backend said it stopped early |
| `request_id` | `str \| None` | The backend's id for the task |
| `recording_url` | `str \| None` | A recording of the task, when returned |
| `raw` | `list[dict[str, Any]] \| None` | Untouched payloads; kept out of `repr()` and `model_dump()` (they can carry session URLs) |

**`BrowseStep` fields:** `action`, `url`, `detail`, `screenshot_url` (all `str | None`), `extra`. Steps are only what the backend reported, never reconstructed from the answer.

**`structured` semantics:**
- Set **only when the backend says the output conforms to a schema the caller requested**, through `output_schema` or a vendor-native schema in `provider_options`.
- A dict whose keys the agent chose stays in `raw` only.

## 5. Capability Negotiation and Fail-Loud

A backend declares which parameters it maps in `supported_parameters`. Anything outside that set raises `UnsupportedParameterError`. A search that quietly ignores `country="US"` is worse than one that fails.

### Parameter validation

```python
# Brave maps only max_results, so this raises
WebRouter(Provider.BRAVE).search("...", country="US")
# UnsupportedParameterError: Provider 'brave' does not map 'country'. Pass it through
# provider_options if the underlying API supports it, or add the mapping -- see CONTRIBUTING.md.
```

If a provider gains support for a parameter, it is added to that set, never special-cased at the call site.

### Format validation

```python
# Exa extract returns only text, so this raises
WebRouter(Provider.EXA).extract(["https://example.com"], format="html")
# UnsupportedFormatError: Provider 'exa' cannot return 'html' content; it returns 'text'. ...
```

### Structured output validation

```python
# Perplexity research does not map output_schema yet, so this raises
WebRouter(Provider.PERPLEXITY).research("...", output_schema=MySchema)
# UnsupportedParameterError: Provider 'perplexity' does not map 'output_schema'. ...
```

### Value validation

```python
# Browserbase maps stealth as low/high only, so this raises
WebRouter(Provider.BROWSERBASE).open_browser(stealth="off")
# UnsupportedParameterError: Provider 'browserbase' does not support stealth='off'. It serves ...
```

### Validation happens before billing

For a job-shaped call (research, browse) and for opening a session, validation happens before anything is submitted. An option the backend cannot take, or a required one left unset, raises before a backend accepts the task:

```python
# Browserbase's Agents API has no start URL, so this raises before anything is billed
WebRouter(Provider.BROWSERBASE).browse("Find the top story", start_url="https://news.ycombinator.com")
# UnsupportedParameterError: Provider 'browserbase' does not map 'start_url'. ...
```

### Credential validation: a named provider fails loud

When you name a provider explicitly, a missing credential is an error:

```python
# Without TAVILY_API_KEY set, this raises
WebRouter(Provider.TAVILY).search("...")
# ConfigurationError: No API key for provider 'tavily'. Set the TAVILY_API_KEY environment variable,
# or pass provider_config={'tavily': {'api_key': ...}} when constructing the router.
```

Naming a backend says you want that backend, so a missing key means the call cannot run as asked. The router does **not** quietly switch to a keyless alternative.

### Keyless fallback: `auto` only

`auto` is different: it tries backends in order, and when one lacks a credential it moves to the next. This is how keyless search works:

```python
# With no keys at all, this works
WebRouter().search("...")  # served by Tavily's anonymous tier, or a keyless MCP endpoint
```

`auto` may use Tavily's anonymous tier even when `TAVILY_API_KEY` is unset. That is fallback, not demotion: you asked for `auto`, which is a chain, and a member that could serve the request did. `response.provider` names that member: `"tavily"`, `"you-mcp"`, `"exa-mcp"`, `"parallel-mcp"` or `"keenable"`, never `"auto"`.

### Never silently drop

A request carrying an option the backend does not map is an error, not a no-op. Silently running a search with weaker constraints than asked would rot an application's correctness with no visible failure.

## 6. Keyless and Anonymous Tiers

`search` and `extract` work with no credentials at all, through backends that offer anonymous access. `open_browser` falls back to a local browser. `research` and `browse` have no keyless tier, because every backend bills for the run.

### `search` chain

`auto` tries, in order, the backends you hold keys for: **Perplexity, Nimble, Linkup, Tavily, Brave, WebIQ, Parallel, Exa, You.com** (`AUTO_PREFERRED`, the order of the 2026-10-06 provider scan run with `benchmarks/providers/`). Then the keyless tiers (`AUTO_KEYLESS`): **Tavily's anonymous tier, You.com MCP, Exa MCP, Parallel MCP, Keenable `/public`**. Then **Keenable** and **DeepSeek** with a key (`AUTO_APPENDED`: each joined after that scan, so they come after every member already being served, DeepSeek last). The keyless tiers are rate-limited, which is why it is a chain: when one is throttled, the next answers.

Every key you hold comes before the keyless tier, so a key held only for research (Perplexity's, say) also serves and bills plain searches. Pass your own `chain` to change that:

```python
router = WebRouter(provider_config={"auto": {"chain": {"search": ["nimble", "tavily"]}}})
```

**The distinction:** explicit `provider="tavily"` without a key **errors** (fail-loud). `provider="auto"` without a key **works** (fallback). The same backend serves both: the authenticated tier when you name it with a key, the anonymous tier when `auto` reaches it.

### `extract` chain

`auto` tries the backends you hold keys for (**Nimble, Tavily, Firecrawl, Oxylabs, Bright Data**), then the keyless ones (**Tavily's anonymous tier, Jina Reader, Firecrawl's keyless tier, Keenable `/public`**), then **Linkup, Exa, WebIQ, Keenable and DeepSeek** with a key (`AUTO_EXTRACT_APPENDED`). Each request goes only to members that can return the format it asks for. Jina Reader needs no key; `JINA_API_KEY` only raises its rate limit. Firecrawl's keyless tier is capped per IP per day. `max` is not a member of this chain.

```python
WebRouter().extract(["https://example.com"])  # no key needed; markdown by default
```

### `open_browser` fallback

`auto` tries the vendors you hold keys for (**Steel, Browserbase, Hyperbrowser, Anchor, Kernel**), then **`local`**, which launches Chrome or Chromium on this machine. `local` comes last: a key in the environment says which vendor you want, so `local` is the fallback, never a substitute. Kernel joined after the others, so it is last among the vendors and no call they already served moves.

```python
with WebRouter().open_browser() as browser:  # opens a local Chromium when no vendor key is set
    browser.goto("https://example.com")
```

### `research` and `browse`: no keyless tier

`auto` tries the backends you hold a key for (research: **Nimble, Parallel, Perplexity, Linkup, You.com**; browse: **Browser Use, Skyvern, Browserbase**) and then stops, naming the variables that would fix it:

```python
WebRouter().research("...")
# ConfigurationError: No research backend is configured. Every one of them bills for the run, so `auto`
# has no keyless tier to fall back on -- set one of ..., or pass provider_config to the router.
```

### Named providers always require their credentials

Naming a provider is never quietly demoted to a keyless tier, even when the same backend offers one that `auto` can reach. Asking for a provider by name says you want its authenticated tier; a missing credential fails rather than switching tier or backend without telling you.

## 7. Adapter Contract and Fallback Policy

Layer 1 is implemented in **Python**. There is one router class per concurrency model, one request and response model per capability, and one provider registry per capability.

### Python

```python
from web_router import WebRouter, AsyncWebRouter, Provider

# Sync
router = WebRouter(Provider.NIMBLE, provider_config={"nimble": {"timeout": 30}})
response = router.search("...", max_results=5)

# Async
async with AsyncWebRouter(Provider.NIMBLE) as router:
    response = await router.search("...", max_results=5)
```

Provider names are case-insensitive strings or `Provider` enum members. Credentials come from the environment (`NIMBLE_API_KEY`, `TAVILY_API_KEY`, ...) or from `provider_config` at construction, which is the only place they are passed in code.

**`provider_config` reaches `auto`'s members.** A key or client setting passed for a backend applies whether you name that backend or reach it through `auto`, and a backend given an `api_key` in `provider_config` joins `auto`'s default chain as one keyed in the environment does.

**Bring your own Nimble client.** The three Nimble classes (search, extract, research) accept a caller's own `AsyncNimble` from `nimble-python` as `async_client`, directly or through `provider_config`:

```python
from nimble_python import AsyncNimble

client = AsyncNimble(api_key=user_key)
async with AsyncWebRouter(provider_config={"nimble": {"async_client": client}}) as router:
    await router.search("query")  # auto counts Nimble as configured; no NIMBLE_API_KEY needed
```

Such a provider reads no key and builds no sync client: a sync call raises `ConfigurationError`, and so does constructing a synchronous `WebRouter` with a client in `provider_config`. The client stays the caller's: it is not closed on `aclose()`, and an `api_key` passed beside it raises `ConfigurationError` rather than replacing its credential.

**Entry points.**

| Surface | Entry point | Extra |
|---------|-------------|-------|
| Library | `from web_router import WebRouter, AsyncWebRouter` | `search`, `extract`, `research`, `browser`, `all` (named after methods, not vendors) |
| MCP server (stdio, self-hosted) | `web-router-mcp` console script (`web_router.mcp.server:main`) | `mcp`; add `browser` for the `browser_*` tools |
| Claude tools | `web_router.integrations.anthropic.WebRouterTools` | none |
| OpenAI tools | `web_router.integrations.openai.WebRouterTools` | none |
| LangChain tools | `web_router.integrations.langchain.web_router_tools` | `langchain` |
| Evals | `router.evaluate(...)`, `web_router.evals.Dataset` | `evals` |

The agent integrations expose two tools, search and extract (`web_router_search` / `web_router_extract` for Claude and OpenAI; `web_search` / `web_extract` for LangChain). The model supplies only the query or the URLs; the provider and options are fixed when the tools are built. The model reads a compact rendering of each result (text cut to a fixed length), and the caller keeps the full response. `tools.guide()` / `routing_guide()` return a static routing guide: fixed text on which tool to use when. These tools do not plan per query. Passing `decider=` on `WebRouter` opts in to `classify()` and `execute()` (Smart Routing v0). Without `decider=`, default routing is unchanged, and these tools do not call that spike.

**Install.** The package is not published to any package index. It is installed from a clone of this repository: `make setup` in the clone (every extra), then `uv add --editable "/path/to/webrouter[all]"` or `pip install -e "/path/to/webrouter[all]"` from your own project, as the README describes. A missing extra's error names the `uv sync --extra <name>` to run. Publishing is a later decision and is not part of this contract.

### Other languages

No other language adapter exists. A TypeScript adapter has been discussed; if one is built, it should map the same request and response shapes and the same fail-loud rules defined here.

### Fallback policy: `auto`

`auto` is a chain: it tries backends in order and falls back when one cannot serve the request. The default orders are `AUTO_PREFERRED` / `AUTO_APPENDED` / `AUTO_KEYLESS` (search), `AUTO_EXTRACT_PREFERRED` / `AUTO_EXTRACT_KEYLESS` / `AUTO_EXTRACT_APPENDED` (extract), `AUTO_RESEARCH_PREFERRED`, `AUTO_BROWSER_PREFERRED` / `AUTO_BROWSER_KEYLESS` and `AUTO_BROWSE_PREFERRED`, in `constants.py`. A caller replaces any of them with `provider_config={"auto": {"chain": ...}}`, as a list for every method or keyed by method (`search`, `extract`, `research`, `browser`, `browse`). Changing a default order is a PR backed by a provider scan (`benchmarks/providers/`).

#### Non-billed chains fall through on any provider failure

For `search` and `extract`, the chain tries the next member on a missing extra, a missing credential, or any `ProviderError` from the backend (4xx, 5xx, connection error, timeout). A bug-shaped error such as `UnsupportedParameterError` propagates.

#### Billed chains fall through only when nothing started

For `research`, `open_browser` and `browse`, the chain tries the next member **only when the failure proves nothing was created**. Once a member may have accepted a run, a session or a task, it is being paid for, so a second one is never started.

Failures that try the next member:
- Missing credential or missing setting (`ConfigurationError`, e.g. `BRIGHTDATA_ZONE` unset)
- Missing extra (`ProviderNotInstalledError`)
- A 4xx answer
- A connection that never reached the backend

Failures that raise immediately:
- 5xx (may come after the vendor created the resource)
- Read timeout (the backend may have accepted)
- An unreadable 200

#### `auto` routes around a member that cannot serve the request

Each member is checked against the request **before it is tried**, on its class, which costs no credential and sends nothing. So `auto` accepts every option some member maps (the union, not the intersection), and each request goes only to members that map everything it sets. When none is left, it raises `UnsupportedParameterError` or `UnsupportedFormatError` naming what each member lacked, and nothing is sent. Research `auto` accepts `effort` and `output_schema`, and a member that does not map `output_schema` is passed over.

Examples: `auto` search with `include_domains` passes over members that do not map domain filters. `auto` extract with `format="html"` passes over Tavily and Exa. `auto` browser with `stealth="low"` passes over `local` and Steel. `auto` browse with `start_url` set passes over Browserbase.

The browser checks are `refused_values` (a value outside the member's support), `unmet_requirements` (an option whose partner the request left unset) and `missing_parameters` (a required option unset).

#### The response, handle or job names the member that served it

```python
response = WebRouter().search("...")
print(response.provider)  # "perplexity", "tavily", "you-mcp", ...; never "auto"

with WebRouter().open_browser() as browser:
    print(browser.provider)  # "steel" or "local", ...; never "auto"
```

The handle or job is the member's own, so its release or poll goes to that member.

## 8. Selection Criteria Vocabulary

Layer 1 does not choose a backend based on task, quality or budget. It routes by name, or by the `auto` chain's fixed order. This section names the criteria a future Layer 2 would select on, so that measurements made now use the same words.

### Quality

Result relevance, extraction accuracy, task success rate. Measured per backend, per query class, and per domain where it matters. A distribution, not a single number.

### Latency

Request to first byte and to completion. Reported as a distribution (p50, p90, p99), not a mean. Varies by query length, result count, page size and time of day.

### Cost

What the backend bills per call, per result, per token, or per minute. Reported in dollars per unit, with the unit named.

### Freshness

How recent the indexed content is: "indexed within N hours of publication" for news, "crawled within N days" for the general web.

### What exists today

- `benchmark()` measures latency, result counts and errors for one query across providers. `benchmark_extract()` does the same for one URL list across extract providers.
- `evaluate()` measures judged accuracy, failure rate and latency percentiles over a question set.
- The repo's provider scan (`benchmarks/providers/`) and agent benchmark (`benchmarks/agent/`) use these to compare backends and to compare an agent on its platform's built-in search against the same agent on web-router's tools and static routing guide.

These are measurement tools. `auto`'s order is a fixed default chosen from a scan, not a per-query decision. Where the agent benchmark shows token savings, they come from the compact result rendering and the static routing guide. Default routing stays that fixed order unless the caller passes `decider=`, which opts in to `classify()` and `execute()` (Smart Routing v0).

## 9. Conformance and "Readable Standalone"

This document is meant to be **readable standalone**: someone new to web-router can read it, understand what the library does, and write a working call without opening another file.

### What "readable standalone" means

1. **Purpose is stated up front.** Section 1.
2. **Capabilities are named and described.** Section 2: every method, its latency, its cost model, and an example.
3. **Request shapes are concrete.** Section 3: every field, its type and meaning.
4. **Response shapes are concrete.** Section 4: every field, its type and meaning.
5. **Failure modes are explicit.** Section 5: what raises, when, and why.
6. **Keyless paths are documented.** Section 6: which capabilities work with no credentials, and which do not.
7. **Adapter contract and fallback policy.** Section 7: how to call it, its entry points, and the `auto` rules.
8. **Selection criteria are named.** Section 8. Layer 2 is out of scope. `classify()` / `execute()` with `decider=` is an opt-in spike, not that layer.

### What this document is not

- **Not an API reference.** That is the docstrings in `src/web_router/types.py` and `src/web_router/client.py`.
- **Not a tutorial.** That is `examples/` and the README's quickstart.
- **Not a provider integration guide.** That is `CONTRIBUTING.md`.
- **Not a deployment guide.** The MCP server is documented in `docs/mcp.md`.

### Conformance

This checklist states what the package must do:

1. Every request model has the fields listed in section 3, with the types listed.
2. Every response model has the fields listed in section 4, with the types listed.
3. A parameter a provider does not map raises `UnsupportedParameterError`; it is never silently dropped.
4. A format a provider cannot return raises `UnsupportedFormatError`; it is never substituted.
5. A value a provider cannot serve raises `UnsupportedParameterError`; it is never swapped.
6. `raw` carries the untouched backend payload.
7. `extra` carries unmapped provider fields.
8. `response.provider` names the backend that actually answered, never `"auto"`.
9. Keyless chains work with no credentials, as in section 6.
10. Billed chains validate before submitting and never start a second run, session or task, as in sections 5 and 7.

The Python package also provides:
- `WebRouter` and `AsyncWebRouter`, importable from `web_router`.
- A `Provider` enum of built-in backend names, and `provider_name(...)` (in `web_router.types`) normalizing a provider argument to lowercase.
- `available_providers()`, `available_extract_providers()`, `available_template_providers()`, `available_research_providers()`, `available_browser_providers()`, `available_browse_providers()`: every registered name per capability.
- `configured_providers()` and its per-capability counterparts, including `configured_template_providers()`: the names this environment holds a credential for, plus the keyless ones (environment variables only).
- `register_provider()` / `unregister_provider()` and per-capability counterparts, including `register_template_provider()`, for backends added outside the package.

## 10. Layer 2: Not Built

Layer 2 is **out of scope for this document**. If it is built, it would own:

- **Task routing:** which backend for this query, based on the query, the result requirements and the caller's budget.
- **Budget-aware selection:** "cheap is good enough here" vs. "this is worth a deep search."
- **Quality optimization:** using measured relevance, accuracy and success rate per backend and query class.
- **Cost and latency trade-offs:** the cheapest backend that meets a quality bar, or the fastest that fits a budget.
- **Freshness requirements:** sending news queries to backends with recent indexes.
- **Structured output negotiation:** when a backend cannot structure its answer, using one that can.

Layer 2 parameters (query classification, budget envelopes, quality thresholds) are **not part of Layer 1** and would be specified separately.

0.7.0 ships `classify()` and `execute()` when the caller passes `decider=` (Smart Routing v0). The spike picks a method and an auto chain before the call. Without `decider=`, default routing is unchanged. The library documents the spike as not a router contract, so this document does not specify its labels.

Layer 1 does not pre-build Layer 2. It defines the adapter contract and the fail-loud policy so that anything added later can build on a stable surface without breaking it.

---

## References

- **Repository:** [WebContextLayer/webrouter](https://github.com/WebContextLayer/webrouter) (private)
- **README:** `README.md`: quickstart, provider tables, keyless paths
- **Install:** `README.md` (Install) and `CLAUDE.md`: clone, `make setup`, editable install
- **MCP server:** `docs/mcp.md`: self-hosted stdio server, browser tools
- **Agent guidance:** `AGENTS.md`: rules for AI coding agents
- **Contributing:** `CONTRIBUTING.md`: adding a provider, promoting an option
- **Examples:** `examples/`: runnable scripts per topic

## Status and Verification

**Package version described:** 0.7.0 (2026-10-26, `src/web_router/_version.py`, `main` at `10cddcd`). Releases since the 2026-10-08 alpha snapshot through 0.7.0 include Nimble `async_client`, Keenable, You.com research, DeepSeek search and extract, Kernel browser sessions, saved browser profiles, `control_url()`, Extract Templates, `max` extract, and `benchmark_extract()`. `classify()` and `execute()` ship only when `decider=` is set (Smart Routing v0) and are outside this contract. Install is from a clone; there is no package index.

**Earlier verification (package 0.0.1, before the repository moved; not re-run on 0.7.0):**
- Keyless search and extract (Tavily anonymous tier, You.com MCP, Jina Reader) passed.
- Keyed search and extract on several providers passed.
- Explicit `provider="tavily"` without a key raised `ConfigurationError`, as specified.
- `auto` without a key served from an anonymous tier, and `response.provider` named the real backend.
- Host-side wrapping of the package functions in an AI SDK Python app (`@ai.tool`, Agent, OpenAI) passed. There is **no** shipped `web_router.ai_sdk` module.

**Shipped but not re-verified here:** `research`, `browse`, `open_browser`, templates, `max`, the agent integrations, `benchmark()`, `benchmark_extract()`, `evaluate()`, and `classify()` / `execute()`. The README records how far each backend has been verified (live, against SDK types, or from documentation only).

**Next step:** re-run the checks above on 0.7.0 and record the commit SHA here.

**Not built:**
- Other language adapters (section 7).
- Layer 2 (section 10).

This is a **working contract draft**. It describes what the 0.7.0 package does and the rules it follows. Sections 3 and 4 tabulate search, extract, research, open_browser and browse. Templates are summarized in section 2; their request and response models are not tabulated here.
