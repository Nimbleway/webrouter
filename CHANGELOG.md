# Changelog

All notable changes to `web-router` are recorded here, newest first. Versions
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html); while on
`0.x`, a breaking change bumps the minor version.

## [Unreleased]

### Added

- `answer()`, a seventh method, and its async twin: `WebRouter.answer(query, provider=...)` asks one question and returns the backend's own written answer with the sources it rests on, in one call -- between `search()`, which returns pages, and `research()`, a job of minutes. `AnswerResponse` carries `answer`, `citations` (the shared `Citation`; an inline marker in the answer goes in `field`), `outcome` (`partial` only when the backend says it stopped early), `request_id` and `raw`, one payload per call. An empty answer is returned as `""`, never raised. The options are `include_domains`, `exclude_domains`, `country`, `locale` and `recency` (`day`, `week`, `month` or `year`); a backend refuses each one it does not map with `UnsupportedParameterError`, before anything is sent. There is no `auto` yet: with `provider` unset the router's default serves the call when it is an answer backend, else the one configured answer backend, and with none or several configured it raises `ProviderNotFoundError` naming them. `AnswerProvider` and `HttpAnswerProvider` to add a backend, `register_answer_provider()`, `available_answer_providers()` and `configured_answer_providers()` beside the other registries, and an `answer` extra, which `mcp` and `all` include. Backends are added one per change.
- `benchmarks/providers/answer.py`, the answer scan: every configured answer backend on the same questions as the search scan, each backend's own answer graded by the same judge, with `--baseline` setting a finished search scan of the same questions and judge beside it. A row citing one of SealQA's own pages is marked leaked and left out of accuracy; cost is shown only where a backend states it. `PRICING.md` gains an Answer section.
- The MCP server's `answer` tool, its twenty-first: the same call as `answer()`, shaped to fit a context window with the answer first, each citation's `field` marker kept, and a handle for `fetch_raw`. `provider` is optional, and only the configured answer backends are offered; with more than one configured and none named, the error lists them. The capabilities resource keys its backends as `answer`, and `unlock` lists the ones a key would add.
- Parallel as an answer backend (`parallel`): its Chat API (beta) with the `lite` model, answering in about seven seconds, the fastest model whose `basis` ties its sources to the answer. Each citation carries the basis's `confidence` in `extra`; a `finish_reason` of `length` is `partial`. The Chat API takes no source filter, so every option is refused. Verified live.
- You.com as an answer backend (`you`): its Answer API on `api.you.com`, answering in about two seconds. Maps `include_domains`, `exclude_domains` and `country` to its own fields and `recency` to `freshness`; it takes one domain list, so asking for both raises before the call, and `locale` is refused. Its citations are only the sources the answer cites, marked `[[1, 2]]`; each carries its marker in `Citation.field` and its title from the pages considered. Verified live.
- Perplexity as an answer backend (`perplexity`): its Agent API at the `fast` effort, one call to `/v1/responses` answering in about two seconds. Maps `include_domains` or `exclude_domains` (its `search_domain_filter`, an excluded domain prefixed `-`; both together are refused before the call), `recency` and `country` (`user_location`) through the `web_search` tool, set only when a filter is; `locale` is refused. The answer cites inline as `[N]`, and each source it cites carries its marker in `Citation.field`. An `incomplete` response is `partial`. Verified live.
- Linkup as the first answer backend (`linkup`): its Search API with `outputType: sourcedAnswer`, answering in about a second. Maps `include_domains`, `exclude_domains` and `recency`, sent as `fromDate` that far back from today; `country` and `locale` have no Linkup field and are refused. Its sources are every page read, not tied to sentences, so `field` stays `None`. Verified live.

### Changed

- `auto` now moves to the next backend when one answers with nothing usable, as it does on an error: a search with no results and no `answer`, or an extract whose every document is blank. A document's page status is not read, since only some backends report one. When every member answers empty, the first empty answer comes back, naming its backend, rather than an error. A call that ends empty has tried every backend that can serve it, one after another, and each one you hold a key for bills for it. `provider_config={"auto": {"fallback_on_empty": False}}` returns the first answer, empty or not; the MCP server has no such switch. `max` is unchanged.

## [0.8.0] - 2026-10-28

### Added

- AWS AgentCore Web Search as a search backend (`agentcore`), through the caller's own AgentCore Gateway, set in `AGENTCORE_GATEWAY_URL`. Requests are SigV4-signed with botocore's usual credential chain, for the region in the gateway's hostname; botocore comes with a new `aws` extra, which `all` includes. Maps `max_results` (at most 25), `include_domains` and `exclude_domains`; on a target older than connector 1.2.0, which takes no filters, the domain options raise rather than being dropped. A date range goes through `provider_options["filters"]["publishedDateFilter"]` and merges with the mapped domains. `auto` tries it last, after DeepSeek, so no existing call changes backend. Setup and teardown: `docs/agentcore-setup.md`.

### Fixed

- Screenshots on Anchor are PNG, as on every other backend. Anchor's browser answers a PNG request with JPEG, even over raw CDP, so `Screenshot.png` held JPEG bytes, `width` and `height` were read from the wrong header (65536 by 4293001688), and the MCP server sent them as `image/png`. A capture that is not PNG is now converted, with Pillow, which the `browser` extra now installs. Checked live on Anchor.

## [0.7.0] - 2026-10-26

### Added

- Templates, a sixth method: `WebRouter.run_template(name, inputs, provider=...)` runs one of a vendor's saved, site-specific extractions (Nimble's Extract Templates today) on one page and returns the record it parsed in `TemplateResponse.data`, `templates(provider=...)` lists one page of the vendor's catalogue as a `TemplatePage`, and `template(name, provider=...)` describes one as a `Template` with the JSON Schema of the inputs a run takes and the record it returns; each has its async twin. A template's inputs are its own -- a URL for some, an id, a slug or a search term for others -- which is why this is a method rather than an option on `extract`, and its name belongs to one vendor, which is why `provider` is required and there is no template `auto`. `format` asks for the page beside the record as `markdown` or `html`; unset, no page comes back. A run the backend could not complete, a page the site answered with 4xx or 5xx, or a parse that failed raises `ProviderError` with the reason, never an empty record; an unknown name raises the new `TemplateNotFoundError`. Nimble is the first backend (`NimbleTemplateProvider`, installed by `[extract]`, verified live), with `TemplateProvider` to add another and `register_template_provider()`, `available_template_providers()` and `configured_template_providers()` beside the other registries. The MCP server adds `templates`, `template` and `run_template`, which alone require `provider`, carry handles for `fetch_raw`, and are keyed as `template` in the capabilities resource.
- `contracts/<method>/<provider>.json`: for every provider, the wire fields each option value sends, read from the provider's own code by `make contracts`, beside the vendor's reference pages, where each mapping was verified (live or in the docs) and the vendor's options no mapping sends. CI fails when a contract no longer matches its provider or leaves a hand-written field empty, so a mapping cannot change without its contract. Extract Template backends have one too. [AGENTS.md](AGENTS.md#provider-contracts) says how to start one.
- `max`, an extract provider that sends the request to every backend `auto` would try, all at once, and returns the first document back -- `WebRouter(Provider.MAX).extract(url)`, with `response.provider` naming the backend that answered. Every fetch it starts is billed by its backend, so it costs up to one extract per configured backend per URL -- most backends take one URL per request, and a loser keeps fetching after the winner returns -- and is never the default. Only a document wins: a backend that refused, could not be built or returned nothing is its own loss, and when every one loses the error names each reason. `provider_config={"max": {"hedge": 0.8}}` runs the first member alone for that long and starts the rest only if it has not answered, so most one-URL calls stay at one fetch; a first member that fails early, or answers only part of the list, starts the rest at once. The hedge covers the first member's whole call, so it does not cap the cost of a multi-URL request. The members are `auto`'s: a `chain` or `members_config` set under `"auto"` in `provider_config` applies to `max` too, unless its own `"max"` entry sets one. `format` routes the same way, and the sync router's `close()` waits for a race's losers before shutting their clients while the async router cancels them. `max` joins `auto` in `BENCHMARK_EXCLUDED`, and the MCP `extract` tool offers it under `provider` with its cost in the description.
- `WebRouter.benchmark_extract()` and its async twin: the same URLs fanned out across several extract backends at once, through the public `extract` path, one `ExtractBenchmarkRun` per provider in an `ExtractBenchmarkReport` -- measured facts only, as `benchmark()` reports for search. A backend that fails, cannot return the format, has no credentials or runs out of the comparison's `timeout` (`BENCHMARK_EXTRACT_TIMEOUT`, 120 seconds) is its own row's error, and a call that fetched some URLs and not others is an answered row whose response says which. `BenchmarkRun` and `BenchmarkReport` now share a base with the extract pair; their fields and behaviour are unchanged, except that a row for a backend that could not be built reports a latency of exactly zero on the async router too.
- `benchmarks/providers/extract.py`: every configured extract backend on the same URLs, tabulated per backend (pages returned with text, empty, failed, timed out, refused, latency, median length) and per category, with a suggested `auto` extract order. Two URL sets ship under `benchmarks/providers/urls/` -- `general` (455 URLs) and `social-pdf` (908 URLs), URL lists sampled from public pages -- and `--urls` takes a CSV of your own. The scan writes `run.json` before its first call and each URL's rows as it finishes, so one that dies keeps what it paid for, and `--combine` on that folder alone reports the rows it fetched against the URLs asked. `examples/compare_extract_providers.py` shows the method on one URL.
- `WebRouter.classify()` and `WebRouter.execute()`, an opt-in spike (unchanged unless you pass `decider=`). `classify(query)` asks a decider one set of questions (tool, recent, coverage, well_scoped) and turns the labels into a `Route`: the method and the auto chain to run it on, searching nothing. `execute(route)` runs it. Passing `urls` routes to extract and `output_schema` to research without asking; a decider that fails, or answers below 0.5 confidence, gives today's default, search on the default chain, with `Route.fallback` saying why (`decider_error`, `low_confidence`, `no_url`, `no_backend_for_method`). The decider is swappable: `WebRouter(decider=...)` takes `"keywords"` (simple keyword rules, keyless, with fixed confidences that are not measured; a research or browse pick sits below the threshold, so it falls back to search), `"jev"` (TypeSafe's Jev, reading `TYPESAFE_API_KEY`), `"llm"` (a chat model with structured outputs over any OpenAI-compatible API, with the `evals` extra, taking `api_key=` or reading `OPENAI_API_KEY`; its version carries a hash of its prompt and schema, `gpt-4.1-mini+p<8 hex>`), a name given to `register_decider()`, or any object with `decide()` and `close()`. `decider_config=` passes constructor kwargs to a decider given by name, as `provider_config=` does for backends. Every built-in decider reports confidence on Jev's normalized scale, `(p - 1/n)/(1 - 1/n)` (`normalized_confidence()`), and the llm decider scales its probabilities to sum to 1 before they are checked; an answer it cannot read takes the `decider_error` fallback. A decider that raises anything, not only `ProviderError`, takes the `decider_error` fallback. Exception types a decider names in its `passthrough` attribute propagate instead of becoming a default route.
- Browser Use now maps `output_schema`, `max_steps` and `allowed_domains`, each checked against a live task on 2026-10-21; they were refused before. The schema is sent as Browser Use's `structuredOutput` and the answer comes back as `structured`; an answer that is not JSON stays in `answer`. `auto` can now serve a browse call that sets any of the three, through Browser Use.

### Fixed

- `benchmarks/providers/run.py` writes `run.json` before its first search and each depth's rows to `rows.jsonl` as that depth finishes, so a scan that dies keeps what it paid for; it wrote everything at the end, so a dropped connection or a Ctrl-C an hour in lost every row. `--combine` on that folder alone reports it, naming the backends whose depth never finished.
- Nimble enrichments (`research` with `records`) work as documented. Nimble takes a table schema for one, so the row schema you pass as `output_schema` is now sent as an array of rows; it was sent as is and refused with a 422. `records` without an `output_schema`, or at `effort="low"`, which Nimble also refuses, now raise before a run starts. Checked live.
- Browser Use steps now carry a `detail` and an `action`. `detail` came from `nextGoal`, which live tasks always return empty, so it was always `None`; it now reads the step's `memory`. A step that took a single action, which every step in four live tasks did, now has it as `action`. `nextGoal` moves to `extra`.

## [0.6.0] - 2026-10-20

### Added

- Logging in once and reusing the login on vendor browser sessions. `profile` and `persist_profile` are now mapped on Browserbase (a context), Steel, Hyperbrowser and Anchor (a profile name), as they already were on Kernel, so a session opened on a profile with `persist_profile=True` saves its cookies and storage when released, and the next session on that profile starts logged in. The handle's new `control_url()` returns a page where a person can see and drive the session to log in: Browserbase's live view, fetched on first call, and the interactive live view each other vendor returns on open. It is a credential like `cdp_url`, kept as a `SecretStr` on the new `BrowserSession.control_url` and out of `repr` and serialization; `live_view_url` stays for pages that grant no control. All of it was checked live on 2026-10-20 on all five vendors. `persist_profile` without a `profile` raises. `auto` refuses `profile` and `persist_profile`, since a profile is saved on one backend; name that backend. The MCP server's `browser_open` still takes no profile, and never shows a control URL.
- Kernel as a browser session backend (`kernel`), over its Browsers API, reading `KERNEL_API_KEY`. It maps `idle_timeout`, `stealth`, `headless`, `viewport`, `profile` and `persist_profile`, each checked against a live session. Kernel has an idle limit and no lifetime, so `timeout` raises and no default lifetime is sent; Kernel's own 60-second idle limit applies unless you set `idle_timeout`. `stealth` also brings Kernel's CAPTCHA solver and stealth proxy, so `solve_captchas` raises, and `proxy_country` raises because Kernel needs a proxy created first; pass an existing one through `provider_options`. `auto` tries Kernel after the other vendors and before `local`, so no existing call changes backend.

### Changed

- Steel's `debugUrl`, Hyperbrowser's `liveUrl` and Anchor's `live_view_url`, which were only in `raw`, are now returned by `control_url()`. Each grants control of the browser, so none of them becomes `live_view_url`, which is unchanged.

## [0.5.0] - 2026-10-19

### Added

- DeepSeek as a search and an extract backend (`deepseek`), reading `DEEPSEEK_API_KEY`. Search maps `max_results`, `include_domains`, `exclude_domains` and `locale`; the filters go inside the query, and a query over DeepSeek's 128-character limit is refused, not cut. `search_depth` and `country` raise. A result's content is DeepSeek's summary unless `need_content` is sent through `provider_options`. Extract returns `markdown`, starting at most 5 fetches a second, DeepSeek's account limit; `ExtractProvider.min_start_interval` lets any backend space its fetches this way. `auto` tries DeepSeek last in both chains, so no existing call changes backend.
- You.com as a research backend (`you`), over its Research API on `api.you.com`, reading the same `YOU_API_KEY` as search. A run is a background task, polled until it finishes; the answer is You.com's markdown and its sources become citations. `effort` maps `low`, `medium`, `high` and `max` onto `lite`, `standard`, `deep` and `exhaustive`; `frontier` is reachable through `provider_options`. `output_schema` is not mapped yet, and You.com offers no cancel. `auto` tries You.com last in the research chain when you hold its key, so no call that was already being served moves. If `YOU_API_KEY` was the only key you held, `research()` through `auto` now starts a billed You.com run instead of raising `ConfigurationError`.

### Fixed

- Jina: an empty `JINA_API_KEY` is treated as no key. It was sent as a bare `Bearer ` header, which httpx rejects, so every Jina call failed and `auto` quietly moved on to the next extract backend. Every provider now strips its API keys and settings, so a whitespace-only value counts as missing, a padded one such as a key with a trailing newline is sent without the padding, and `configured_*_providers()` no longer lists a provider whose only key is blank.
- A research run or browse task whose status request stalled until the caller's `timeout` ran out raised `ProviderError`, which carries no job id, instead of `ResearchTimeoutError` / `BrowseTimeoutError`, so a run that was still going, and billing, could not be come back to. A poll whose request timed out once the deadline has passed now raises the method's timeout error with the job id, the failure as its cause. Any other poll failure -- one with time left, or an auth error or unrecognized status after the deadline -- still raises `ProviderError`.
- Bright Data treats a URL its unlocker could not fetch, such as an unresolvable host, as a failure with Bright Data's reason and error code, instead of returning it as an empty document with status 502. A single-URL `extract()` raises `ProviderError`, and `auto` moves on to the next extract backend; in a multi-URL call the URL is listed in `ExtractResponse.failures` beside the pages that were fetched. A page the target site served, an error page included, is still a document.

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
