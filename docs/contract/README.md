# Contract Reference

The web-router contract defines how providers normalize their requests and responses into one shared shape, so code written against one backend works with any other.

## Status

The contract is defined by:

1. **Type definitions** in `src/web_router/types.py`
2. **Base provider classes** in `src/web_router/providers/base.py`
3. **The contract tests** listed below
4. **This page** and the [guides](../guides/adding-a-provider.md)

The code is the source of truth. The package has six methods: `search`, `extract`, `run_template`, `research`, `open_browser` and `browse`. A longer working draft of the Layer 1 contract is in review separately (draft PR #16, `docs/spec/layer-1-contract.md`); until it lands, this page and the code are the reference.

This page covers search and extract in detail. Templates, research, browser sessions and browse follow the same rules (unset means unset, unmapped raises, untouched payload in `raw`); their models are in `types.py` and their contract tests are listed below.

## Core contracts

### Search contract

**Request:** [`SearchRequest`](https://github.com/WebContextLayer/webrouter/blob/main/src/web_router/types.py)

- `query: str`: the search query (at least 1 character)
- `max_results: int | None`: upper bound on results returned
- `search_depth: SearchDepth | None`: content richness (`lite`, `fast`, `deep`)
- `include_domains: list[str] | None`: only search these domains
- `exclude_domains: list[str] | None`: exclude these domains
- `country: str | None`: ISO country code for geo-targeted search
- `locale: str | None`: language code, e.g. `"en"`
- `provider_options: dict[str, Any]`: provider-specific options, passed through untranslated (defaults to `{}`)

**Response:** [`SearchResponse`](https://github.com/WebContextLayer/webrouter/blob/main/src/web_router/types.py)

- `provider: str`: which backend answered (never `"auto"`)
- `query: str`: the query that was run
- `results: list[SearchResult]`: normalized results, in rank order
- `answer: str | None`: a synthesized answer, if the provider returned one
- `request_id: str | None`: the provider's request id, if given
- `raw: dict | None`: untouched provider payload
- `urls` (property): every result URL, in rank order

**Result:** [`SearchResult`](https://github.com/WebContextLayer/webrouter/blob/main/src/web_router/types.py)

- `url: str`: result URL
- `title: str`: page title (empty string if not returned)
- `content: str`: the fullest text available (the page body, otherwise the description)
- `description: str`: a snippet-length summary: the provider's own when it gives one, otherwise the start of the page body
- `score: float | None`: relevance score, if the provider returned one
- `published_date: str | None`: publication date, if available
- `extra: dict`: unmapped provider-specific fields

### Extract contract

**Request:** [`ExtractRequest`](https://github.com/WebContextLayer/webrouter/blob/main/src/web_router/types.py)

- `urls: list[str]`: one or more URLs to fetch
- `format: ExtractFormat | None`: `markdown`, `html` or `text`. Unset asks for `markdown` from every backend that can return it.
- `provider_options: dict[str, Any]`: provider-specific options, passed through untranslated

**Response:** [`ExtractResponse`](https://github.com/WebContextLayer/webrouter/blob/main/src/web_router/types.py)

- `provider: str`: which backend answered
- `documents: list[ExtractedDocument]`: fetched pages, in the order their URLs were asked for
- `failures: list[ExtractFailure]`: URLs that did not come back
- `request_id: str | None`: the provider's request id (the first one, for backends that take one URL per call)
- `raw: list[dict] | None`: untouched provider payloads, one per API call
- `urls` (property): every document URL, in request order

**Document:** [`ExtractedDocument`](https://github.com/WebContextLayer/webrouter/blob/main/src/web_router/types.py)

- `url: str`: the URL this document came from
- `content: str`: page content in the format `format` names
- `format: ExtractFormat | None`: which representation `content` holds
- `title: str`: page title (empty string if not available)
- `status_code: int | None`: HTTP status of the fetched page (not a failure indicator)
- `published_date: str | None`: publication date, if available
- `extra: dict`: unmapped provider-specific fields

**Failure:** [`ExtractFailure`](https://github.com/WebContextLayer/webrouter/blob/main/src/web_router/types.py)

- `url: str`: the URL that failed
- `error: str`: why it failed

## Invariants

These rules are enforced by the base classes and the contract tests, and must hold for every provider.

### Search invariants

1. **No invented data**: a field the provider did not return stays `None` or empty. No synthesized scores or dates.
2. **All results have URLs**: `url` is the only required field.
3. **Depth mapping is distinct**: every search depth level a provider maps produces a different API call.
4. **Raw is preserved**: `SearchResponse.raw` holds the provider payload.

### Extract invariants

1. **Format accuracy**: `document.format` is what the backend actually returned. A backend that cannot return the requested format raises `UnsupportedFormatError`; it never relabels another format.
2. **One default**: with no `format`, every backend that can return markdown returns markdown. One that cannot (Exa, text only) returns its own default, and `format` says so.
3. **Order preservation**: documents come back in the order their URLs were requested.
4. **Partial success**: failed URLs go in `failures`, successful ones in `documents`. A call where nothing came back raises `ProviderError` instead.
5. **All URLs accounted for**: every input URL appears in either `documents` or `failures`.
6. **Batch transparency**: fan-out is invisible to callers; single-URL providers behave like batching ones.
7. **Raw is preserved**: `ExtractResponse.raw` holds one payload per API call.

### Shared invariants

1. **Unset means unset**: `None` fields are not sent to the provider.
2. **Unsupported raises**: unmapped options raise `UnsupportedParameterError`; they are never silently dropped.
3. **Errors are wrapped**: provider failures become `ProviderError`, with the original on `__cause__`.
4. **Provenance**: `provider` names the backend that answered, never `"auto"`.
5. **No credential leaks**: HTTP providers never follow redirects with an auth header, and a provider's `repr` omits its key.

## Provider responsibilities

When implementing a provider, you are responsible for:

- **Normalizing responses** into the shared models
- **Declaring supported parameters** so unsupported ones raise instead of being dropped
- **Preserving unmapped fields** in `extra` and `raw`
- **Mapping formats correctly** (extract only) and raising for unsupported ones
- **Wrapping failures** in `ProviderError`

The base classes handle:

- **Validation**: rejecting unsupported parameters and formats
- **Error wrapping**: catching exceptions and wrapping them
- **Fan-out** (extract only): calling single-URL providers once per URL
- **Failure assembly** (extract only): collecting failed URLs into one response

See the [Plugin vs Catalogue guide](../guides/plugin-vs-catalogue.md) for registering a provider at runtime vs. adding it to the built-in catalogue.

## Contract tests

Six test modules enforce these contracts. They run offline (no API calls) and check structure:

- [`tests/test_contract.py`](https://github.com/WebContextLayer/webrouter/blob/main/tests/test_contract.py): search
- [`tests/test_extract_contract.py`](https://github.com/WebContextLayer/webrouter/blob/main/tests/test_extract_contract.py): extract
- [`tests/test_template_contract.py`](https://github.com/WebContextLayer/webrouter/blob/main/tests/test_template_contract.py): templates (`run_template`, `templates`, `template`)
- [`tests/test_research_contract.py`](https://github.com/WebContextLayer/webrouter/blob/main/tests/test_research_contract.py): research
- [`tests/test_browser_contract.py`](https://github.com/WebContextLayer/webrouter/blob/main/tests/test_browser_contract.py): browser sessions (`open_browser`)
- [`tests/test_browse_contract.py`](https://github.com/WebContextLayer/webrouter/blob/main/tests/test_browse_contract.py): browse

Add your provider to them when contributing.

## Evolution

The contract changes as options are added. Changes follow these principles:

1. **Additive changes are safe**: a new optional field does not break existing code.
2. **Behavior changes are called out**: a change in what an existing call returns (for example, making `markdown` the extract default) is listed under "Changed" in `CHANGELOG.md`.
3. **Consistency across providers**: a new shared option must mean the same thing on every backend that maps it.

## See also

- [Adding a provider](../guides/adding-a-provider.md): how to implement the contract
- [Promoting an option](../guides/promoting-an-option.md): adding new shared parameters
- [`src/web_router/types.py`](https://github.com/WebContextLayer/webrouter/blob/main/src/web_router/types.py): type definitions
- [`src/web_router/providers/base.py`](https://github.com/WebContextLayer/webrouter/blob/main/src/web_router/providers/base.py): base classes
