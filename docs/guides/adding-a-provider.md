# Adding a Provider

This guide walks through adding a new search or extract provider to web-router. Research, browser and browse providers follow the same shape; see [the last section](#research-browser-and-browse-providers).

There is a base class per method, and a provider serves one or both. A vendor that already serves one gets a second class in its existing module rather than a new file — `nimble.py`, `tavily.py`, `exa.py`, `linkup.py`, `webiq.py` and `keenable.py` each hold a search provider and an extract provider, and `nimble.py`, `linkup.py`, `parallel.py`, `perplexity.py` and `you.py` a research provider too.

## Adding a search provider

### 1. Create the provider class

Create `src/web_router/providers/<name>.py` with a `SearchProvider` subclass setting `name` and `supported_parameters`. You implement the underscored methods; the base class wraps them with validation and error handling, so never override `search`/`asearch` themselves.

**If the API has no Python SDK — or you only need a couple of its fields — subclass `HttpSearchProvider` instead** (`providers/_http.py`). It owns client construction, auth headers, HTTP error mapping and cleanup, leaving you two methods: `_build_payload` and `_to_response`. Brave, Exa, Parallel, Perplexity, Linkup, WebIQ, Keenable and You.com are all built this way and are the shortest examples to copy. Subclass `SearchProvider` directly only when wrapping a real SDK, as Nimble and Tavily do.

`HttpSearchProvider` clients never follow redirects, and a provider sending a credential refuses to. `httpx` replays the auth header to the redirect target even on another host, so if your endpoint redirects, point `base_url` at where it lands rather than turning redirects on.

An endpoint that needs no credential sets `env_api_key = None`: no auth header is sent and a missing key never raises. `you_mcp.py`, `exa_mcp.py` and `parallel_mcp.py` are the worked examples; a keyless hosted MCP endpoint should reuse the shared JSON-RPC transport in `providers/_mcp.py` rather than writing its own. `you_mcp.py` also shows the other case worth knowing — when a second endpoint returns the same payload shape as an existing provider, subclass that provider and override only the transport rather than copying its normalization.

### 2. Map the response

Map the provider's payload onto `SearchResponse`; keep anything unmapped in `SearchResult.extra` and `SearchResponse.raw`. Don't invent values the provider didn't return — no synthesized scores or dates.

### 3. Declare supported parameters

Start with `supported_parameters = {"max_results"}`, plus `search_depth` if the API has a content-depth control — Brave, Exa, Parallel and Perplexity have none. That is the minimum a search needs; send the provider nothing else. Anything outside the set raises `UnsupportedParameterError` rather than being dropped, which is the point: a query must never quietly run without a filter the caller asked for, and the error names the two ways forward.

### 4. Wrap failures

Wrap SDK failures in `ProviderError` so callers can handle every backend the same way.

### 5. Register and configure

Add the SDK to the extra for each method the provider serves in `pyproject.toml` — extras are named per method (`search`, `extract`), never per vendor, so a search backend goes in `search` rather than getting one of its own. Register the module in `_BUILTIN_PROVIDERS` in `src/web_router/registry.py`, and add an entry to `ENV_BY_PROVIDER` in `constants.py` naming the variable its credential comes from — or `None` if it needs none. `configured_providers()` reads that table, and `tests/test_contract.py` fails if a provider is missing from it.

A new search backend does not change `auto`'s order on its own. If it should join the `auto` chain, add it to `AUTO_APPENDED` in `constants.py`, which is tried after the keyless tier, so joining moves no call that is already being served. Moving it ahead of the keyless tier (into `AUTO_PREFERRED`) is a routing change, and that PR should be backed by a provider scan (`benchmarks/providers/`). Keenable is the latest example: it joined search `auto` through `AUTO_APPENDED`, after the keyless tier, and its keyless `/public` tier went last in `AUTO_KEYLESS`.

### 6. Add tests and documentation

Add tests — parameter mapping and response normalization against a recorded payload, plus one `@pytest.mark.integration` live test — and a row in the README support table.

## Adding an extract provider

The same shape, one method along. `firecrawl.py` is the shortest complete example.

### 1. Create the provider class

Subclass `HttpExtractProvider` and implement `_build_payload` and `_to_response`, or `ExtractProvider` and `_extract`/`_aextract` when you are wrapping an SDK. Never override `extract`/`aextract`: the base class runs validation, wraps failures, and fans the URLs out across calls for you.

### 2. Define format support

Put your format mapping in `constants.py` as a table keyed by `ExtractFormat`, and derive `supported_formats = frozenset(ACME_EXTRACT_FORMATS)` from it so the table stays the single description of what the provider can do. Set `default_format` to what the API returns when asked for nothing in particular — check this against a live call, because it is what `ExtractedDocument.format` reports on every call that named no format. Read the format through `format_of()` and send it wherever the API has a field for it; if the API expresses its default by omission, as Oxylabs' and Bright Data's do, send nothing for that one and the label is still right.

### 3. Configure batching

Set `batches_urls = True` only if one call really takes every URL, as Tavily's and Exa's do -- `auto` also sets it, for the different reason that it delegates the whole request to a member. Leave it alone and the base class calls you once per URL and assembles one response, which is what most of these APIs need.

### 4. Map the response

Map the payload onto `ExtractResponse`. If the API answers 200 and reports a refused fetch in a status field — Nimble and Firecrawl both do — return that URL as an `ExtractFailure` rather than a document with empty content. A non-200 status for the *page* is not a failure; it belongs in `ExtractedDocument.status_code`.

### 5. Register and configure

Register the module in `_BUILTIN_EXTRACT_PROVIDERS` in `registry.py`, add an entry to `EXTRACT_ENV_BY_PROVIDER` in `constants.py` — a tuple of every variable the provider needs, `()` if it needs none — and add each variable to `.env.example`. To join extract `auto`, add it to `AUTO_EXTRACT_APPENDED`, for the same reason as on the search side.

### 6. Add tests and documentation

Add `tests/providers/test_<name>.py` and the live checks in `tests/test_integration_surface.py`, which run only against providers declaring the format under test. Add a row to the README's extract support table.

## Special cases

### Non-standard authentication

If your auth scheme is not a single header, override `_auth_headers` rather than building the header yourself — Oxylabs' HTTP Basic is the worked example. Doing it that way keeps `api_key` set, which is what the redirect refusal above keys off; a provider that hides its credential from the base class would quietly opt out of that protection.

### Verifying against live APIs

Check provider behavior against the live API, not just its documentation. We have already been bitten several times: You.com documents an `extraction_formats` field that the service rejects with a 422, Tavily rejects `format="html"` on extract, Jina Reader moves the page body to a different key for each format, and Bright Data's own reference shows a JSON envelope beside a `format="raw"` request that does not return one.

Where no account is available, say so in the test module's docstring the way `tests/providers/test_oxylabs.py`, `test_brightdata.py` and `test_firecrawl.py` do, so the next person knows the mapping is documentation-derived rather than verified.

### Research, browser and browse providers

The same shape again: `ResearchProvider`, `BrowserProvider` and `BrowseProvider` in `providers/base.py`, each with an HTTP flavour in `providers/_http.py` (`HttpResearchProvider`, `HttpBrowserProvider`, `HttpBrowseProvider`). Register them in `_BUILTIN_RESEARCH_PROVIDERS`, `_BUILTIN_BROWSER_PROVIDERS` or `_BUILTIN_BROWSE_PROVIDERS` in `registry.py`, with the matching `*_ENV_BY_PROVIDER` entry in `constants.py`. These backends bill per run or per minute, so two rules matter more here:

- Validate everything before the request that starts the run, session or task. A browse backend that cannot start without an option lists it in `required_parameters`, so the base class raises `MissingParameterError` before anything is billed.
- Report a `partial` outcome only when the backend says so, and never reconstruct steps or citations the backend did not return.

The step-by-step recipes are in `AGENTS.md` ("Adding a research provider", "Adding a browser provider", "Adding a browse provider"). They are written for coding agents but apply to people the same way.

## Checklist

Before submitting a provider PR:

- [ ] Provider class implements `_build_payload` and `_to_response` (or `_search`/`_asearch` for SDK-backed)
- [ ] `name` and `supported_parameters` declared
- [ ] Endpoint, env var, and parameter mappings in `constants.py`
- [ ] SDK added to appropriate extra in `pyproject.toml`
- [ ] Provider registered in `registry.py`
- [ ] Added to `AUTO_APPENDED` / `AUTO_EXTRACT_APPENDED` only if it should join `auto` (reordering is backed by a provider scan in `benchmarks/providers/`)
- [ ] Environment variable(s) added to `ENV_BY_PROVIDER` or `EXTRACT_ENV_BY_PROVIDER` in `constants.py`
- [ ] Environment variable(s) added to `.env.example`
- [ ] Tests in `tests/providers/test_<name>.py` covering parameter mapping and response normalization
- [ ] At least one `@pytest.mark.integration` live test (skips without credentials)
- [ ] Live checks added to `tests/test_integration_surface.py` (if mapping depth/format options)
- [ ] Row added to README support table
- [ ] Entry added to `CHANGELOG.md` under `## [Unreleased]`
- [ ] Live evidence included in PR description (screenshots/logs of successful API calls)

## See also

- [Promoting an option](promoting-an-option.md) — mapping a new shared parameter to existing providers
- [Plugin vs catalogue](plugin-vs-catalogue.md) — runtime registration vs. built-in providers
- `examples/custom_provider.py` — minimal working example
