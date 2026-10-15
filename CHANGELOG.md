# Changelog

All notable changes to `web-router` are recorded here, newest first. Versions
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html); while on
`0.x`, a breaking change bumps the minor version.

## [Unreleased]

### Added

- `WebRouter.benchmark_extract()` and its async twin: the same URLs fanned out across several extract backends at once, through the public `extract` path, one `ExtractBenchmarkRun` per provider in an `ExtractBenchmarkReport` -- measured facts only, as `benchmark()` reports for search. A backend that fails, cannot return the format, has no credentials or runs out of the comparison's `timeout` (`BENCHMARK_EXTRACT_TIMEOUT`, 120 seconds) is its own row's error, and a call that fetched some URLs and not others is an answered row whose response says which. `BenchmarkRun` and `BenchmarkReport` now share a base with the extract pair; their fields and behaviour are unchanged, except that a row for a backend that could not be built reports a latency of exactly zero on the async router too.
- `benchmarks/providers/extract.py`: every configured extract backend on the same URLs, tabulated per backend (pages returned with text, empty, failed, timed out, refused, latency, median length) and per category, with a suggested `auto` extract order. Two URL sets ship under `benchmarks/providers/urls/` -- `general` (455 URLs) and `social-pdf` (918 URLs), URL lists from anonymized third-party evaluations of extraction APIs -- and `--urls` takes a CSV of your own. `examples/compare_extract_providers.py` shows the method on one URL.

## [0.4.0] - 2026-10-14

### Added

- Keenable as a search and an extract backend (`keenable`), over its Search and Fetch APIs, reading `KEENABLE_API_KEY`. Search maps `max_results`; extract returns `markdown` and asks for up to 500,000 characters, where Keenable's own default cuts a page at 50,000. With `allow_keyless=True` both run on Keenable's keyless `/public` endpoints, rate-limited per IP. `auto` tries Keenable after the keyless tier when you hold its key, and its keyless tier last of all, so no call that was already being served moves.

## [0.3.0] - 2026-10-12

### Added

- `NimbleProvider`, `NimbleExtractProvider` and `NimbleResearchProvider` accept the caller's own `AsyncNimble` as `async_client`, directly or through `provider_config={"nimble": {"async_client": client}}`. Such a provider needs no key, builds no sync client (a sync call raises `ConfigurationError`, and so does a synchronous `WebRouter` handed a client in `provider_config`), and leaves the client open on `aclose()`. An `api_key` passed beside the client raises `ConfigurationError` rather than replace its credential. Other options passed beside it apply to a copy that shares its connection pool, and research keeps the client's own timeouts, clamping only the read limit to the run's budget. `auto` counts Nimble as configured when it is handed a client, as it does for an `api_key`.

### Changed

- A missing extra's error now tells you to run `uv sync --extra <name>` in your clone, or `make setup` for every extra, instead of a `pip install` line.
- The MCP server's `web-router://capabilities` resource adds `unlock`: for each method, the backends no key has reached yet, and the environment variables that would add each (names only).
- Project links point at this repository, and the README opens with the alpha notice. The package carries no license metadata during the alpha; the code is shared under your agreement.

## Alpha snapshot - 2026-10-08

The first alpha snapshot, shared with alpha users. Not a published release:
the next published version will be above it.

- **Five methods behind one interface:** `search`, `extract`, `research`
  (agentic, minutes rather than seconds), `open_browser` (a browser session you
  drive, with page actions built in) and `browse` (a hosted browser agent's
  task). Each call behaves the same whichever backend serves it.
- **Many backends,** from Nimble, Tavily, Exa, Brave, Perplexity, Parallel,
  Linkup, You.com, WebIQ, Firecrawl, Oxylabs, Bright Data and Jina Reader to
  Steel, Browserbase, Hyperbrowser, Anchor, Browser Use, Skyvern and a local
  Chromium. Several need no key at all.
- **`auto`,** the default for every method: it tries the backends you hold keys
  for, then the keyless ones, and routes each request to a backend that can
  serve it. Pass a `chain` to choose your own order, per method.
- **An MCP server** (`web-router-mcp`) exposing every method as tools, including
  `browser_*` tools that drive a session, for Claude Code, Claude Desktop,
  Cursor and other MCP clients.
- **Agent integrations:** tools for the Claude and OpenAI APIs, and for
  LangChain, with a routing guide that tells a model which tool fits which need.
- **Benchmarking and evaluation:** `benchmark()` compares backends on the same
  query, `evaluate()` scores their accuracy on a question set with an LLM judge,
  and `benchmarks/` holds the scripts to run both at scale.
