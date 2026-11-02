# Layer 1 Contract: web-router

**Status:** Contract for package **0.8.0** (`src/web_router/_version.py`). The implementation must satisfy this document. It is still pre-1.0: a rule change belongs in this file in the same change as the code. Field inventories stay in [`src/web_router/types.py`](../../src/web_router/types.py). Chain order stays in [`src/web_router/constants.py`](../../src/web_router/constants.py). Per-backend option maps stay in [README.md](../../README.md). This file states the rules those tables have to obey.

Earlier verification ran on package 0.0.1, before this repository moved, and has not been re-run on 0.8.0. See [Status and verification](#status-and-verification).

Layer 1 is the interface contract: one request and one response shape per capability, fail-loud validation, and a fixed-order `auto` chain. Layer 2 (choosing a backend from the task, a budget, or a quality target) is out of scope.

## 1. Purpose and non-goals

### What Layer 1 is for

Each backend has its own parameters and payload. Layer 1 gives the caller one shape per capability so a call behaves the same way whichever backend serves it.

1. **One request shape per capability.** `search(query, max_results=5)` is the same call whichever backend answers.
2. **One response shape per capability.** `result.content` is the page text, `result.url` the source, `result.score` the relevance when the backend gave one.
3. **Fail loud.** A backend that cannot honor an option raises. It does not drop the option and run a weaker call.
4. **Fixed-order fallback.** `auto` tries members in the order in [section 5](#5-auto-chains). It does not pick a backend per query.
5. **No invented data.** A missing score, date, or snippet stays missing. The package does not estimate one.
6. **Provenance.** `response.provider` names who answered. `response.raw` keeps the untouched payload.

### What it is not

- **Not a task router.** Layer 1 routes by name (`provider="tavily"`) or by the fixed `auto` order. `classify()` and `execute()` run only when the caller passes `decider=`. That spike is off otherwise, and it is not Layer 2. See [section 6](#6-calling-the-package).
- **Not a prompt rewriter and not a quality model** on the default path.
- **Not budget-aware.** Layer 1 does not decide that a cheaper backend is good enough.
- **Not a hosted service.** It is a library. Each credential goes only to the backend it belongs to. The MCP server is a stdio subprocess of the caller's client. See [docs/mcp.md](../mcp.md).
- **Not a model-API router.** It unifies web search, extraction, templates, research, written answers, browser sessions, and hosted browse tasks.

```python
from web_router import Provider, WebRouter

router = WebRouter(Provider.TAVILY)
response = router.search("...", max_results=5, search_depth="deep")

router = WebRouter()  # auto: keyed members in section 5, then keyless tiers where they exist
```

The same shape applies to `extract`, `run_template`, `research`, `answer`, `open_browser`, and `browse`.

## 2. Capabilities

Seven methods. Each has its own registry. A name that one method does not serve raises, and the error names the names that method does serve.

The built-in names, and the `auto` tiers, are the block in [section 5](#5-auto-chains). [`tests/test_layer1_contract.py`](../../tests/test_layer1_contract.py) checks that block against the registries and `constants.py`. README tables say which options each backend maps. This section states what each method returns.

### `search`

A query in, ranked results out. Built-in names include `agentcore` (AWS AgentCore Web Search, through the caller's AgentCore Gateway). Its credential is the gateway URL plus the AWS credential botocore resolves, not an API key in this package. Setup: [docs/agentcore-setup.md](../agentcore-setup.md).

### `extract`

Page contents for URLs the caller already has, as markdown, HTML, or text. With no `format`, every backend that can return markdown returns markdown. One that cannot (Exa, text only) returns its own default, and `ExtractedDocument.format` names which. `auto` accepts the union of formats and sends each call only to a member that can return the format asked for.

`max` races the members extract `auto` would try and returns the first document. It is not a member of that chain, it is never the default, and `response.provider` names the member that won. Every fetch a member starts can be billed.

### `run_template`

One of a vendor's saved extractions, on one page, returning the record that template parsed. Inputs are the template's own. `provider` is required. There is no template `auto`. Built-in today: `nimble`. `templates` lists one page of the catalogue. `template` describes one, schemas included. `format` asks for the page beside the record. Unset, no page is requested. A fetch or parse the backend could not finish raises `ProviderError`. An unknown name raises `TemplateNotFoundError`.

### `research`

An agentic run. Latency is minutes, and every backend bills. `auto` has no keyless tier. Exa has no research provider. `submit_research` returns a job. `research(timeout=...)` raises `ResearchTimeoutError` when the wait ends and does not cancel the run.

### `answer`

One question, answered in one call. The backend searches and writes the answer. The response carries that answer and the sources it rests on. This package does not edit the answer. Every backend bills, so there is no keyless tier. There is no `auto` yet. With no `provider`, the router's default serves the call when that default is an answer backend. Otherwise the one configured answer backend serves it, and a key in `provider_config` counts. With none configured, or with several and no default, the call raises `NoAnswerProviderError`.

### `open_browser`

A session handle. Drive it over CDP, or through the handle's page actions (`goto`, `snapshot`, `click`, `type`, `press`, `screenshot`, `read`), which need the `browser` extra. A cloud session bills until it is released. The `with` block releases it on every path, and `router.close()` releases any handle the router still holds. `auto` tries keyed vendors, then `local`.

### `browse`

A hosted browser-agent task. Same job shape as research. Every backend bills. `auto` has no keyless tier. `submit_browse` returns the job. `browse(timeout=...)` raises `BrowseTimeoutError` and does not cancel the task.

### Measurement

`benchmark()` and `benchmark_extract()` report measured facts for one request across providers: latency, counts, the response or the error. No quality score and no price. `evaluate()` adds a labelled judge over search results. Needs the `evals` extra. None of the three changes `auto`'s order. See the README sections for each.

## 3. Request and response rules

Request and response fields are the models in [`src/web_router/types.py`](../../src/web_router/types.py). This contract does not copy those tables. The implementation must keep the following.

### Requests

- Every request model forbids unknown fields.
- A field left `None` is not sent, so the backend keeps its own default. Three exceptions, and no others:
  - Extract `format`: unset asks for markdown from every backend that can return it, and that backend's own default from one that cannot.
  - Browser `timeout`: unset sends `BROWSER_TIMEOUT` (600 seconds) to a backend that maps a lifetime. A backend that maps no lifetime is sent nothing, and naming `timeout` raises. The caller passes a larger `timeout` to go past 600.
  - `local` launches headless when `headless` is unset. There is no vendor default to keep.
- `provider_options` is an untranslated passthrough. It must not override a browser option the provider already maps. The browser payload builder raises `ConfigurationError` when both set the same field.
- The provider is an argument to the router method, not a field of the request model.

### Responses

- `provider` is the backend that answered, never `"auto"`, including when `auto` or `max` dispatched the call.
- `raw` is the untouched payload. `extra` holds provider fields the normalized model does not name. Normalization does not drop either.
- `score`, `published_date`, and any other optional fact stay `None` when the backend did not give one.
- `ResearchResponse.outcome` and `BrowseResponse.outcome` are `"partial"` only when the backend itself reports an early stop. A thin answer is `"complete"`.
- An extract that returns some documents and some failures stays successful. `failures` lists the URLs that failed. The call raises only when nothing came back.
- A CDP URL and a control URL are credentials (`SecretStr`). `raw` on a browser session stays out of `repr` and `model_dump`. `handle.cdp_url` and `handle.control_url()` return the plain string when the caller asks. The MCP server returns neither.

Option meanings that are not a field list (`search_depth` levels, extract formats, research effort, browser stealth) are in the README. A backend maps a level onto its own API. It does not invent a level it cannot tell apart, and it does not substitute a different value for one it cannot serve.

## 4. Fail loud

A provider declares `supported_parameters`. Anything else on the request raises `UnsupportedParameterError` before the backend is called. A format it cannot return raises `UnsupportedFormatError`. A value it cannot serve (a stealth level, a proxy country) raises `UnsupportedParameterError` naming the value. None of these is dropped, relabelled, or swapped.

Naming a provider means that provider. A missing credential raises `ConfigurationError`. The router does not switch to a keyless tier or to another backend.

For research, browse, and opening a session, validation runs before anything is submitted. A missing required parameter raises `MissingParameterError` before billing. An option the backend cannot take raises `UnsupportedParameterError` before billing.

`auto` is the exception to "missing credential fails the call", and only there: a member that cannot be built is skipped, and another member may answer. The rules for that skip are [section 5](#5-auto-chains). An option no remaining member can serve still raises, and the error names what each member lacked.

These checks live on the provider base classes. A provider implements the underscored translation methods and does not override the public method. [`tests/test_contract.py`](../../tests/test_contract.py) guards that pattern for search. The extract, template, research, answer, browse, and browser contract tests guard their methods.

## 5. auto chains

`auto` is the default provider. It tries members in a fixed order. Reordering a default is a change to `constants.py` and to the block below. Search, extract, and research reorderings are backed by a provider scan. A caller passes `provider_config={"auto": {"chain": ...}}` to replace an order: a list for every method, or a mapping keyed by `search`, `extract`, `research`, `browser`, or `browse`.

Runtime order for search and extract: keyed `preferred` members the caller holds, then `keyless` members not already in that chain under a key, then keyed `appended` members the caller holds. A keyless copy of a backend already tried with a key is omitted, because it would send the same call again. Research and browse try keyed `preferred` members only. Browser tries keyed `preferred` members, then `keyless` (`local` is always eligible).

Holding a key puts that backend in the chain. A key held only for research therefore serves plain searches when that backend is a search member, and those searches bill. `agentcore` counts as held when `AGENTCORE_GATEWAY_URL` is set (or `gateway_url` is passed in `provider_config`), because it has no API key of its own. It is last in `search.appended`, so with a gateway set a default search is still served by an earlier member. Name `agentcore` to reach it first.

A member is checked on its class before it is tried. That check costs no credential and sends nothing. Search, extract, browser, and browse `auto` accept an option some member maps, and pass over a member that does not map what this request set. Research `auto` accepts `effort` and `output_schema` only (`AUTO_RESEARCH_SUPPORTED_PARAMETERS`). A research member that does not map `output_schema` is passed over. `records` and the domain filters are not on research `auto`, because only Nimble maps them. When no member remains, the call raises `UnsupportedParameterError`, `UnsupportedFormatError`, or `MissingParameterError`, and the error names why each member was skipped.

Non-billed chains (`search`, `extract`) try the next member on a missing extra, a missing credential, or a `ProviderError` from the backend. `UnsupportedParameterError` propagates.

Billed chains (`research`, `open_browser`, `browse`) try the next member only when nothing was created: a missing credential or setting, a missing extra, a 4xx, or a connection that never reached the backend. A 5xx, a read timeout, or an unreadable 200 raises. The chain does not start a second billed run, session, or task. The handle or job `auto` returns is the member's own.

The lines in the block are normative. Names are the provider strings. `tests/test_layer1_contract.py` fails when the registries or `constants.py` disagree with it. The `package` line equals `web_router.__version__`. A release that bumps the version updates that line in the same change. The test keeps the match on purpose. Release automation does not edit this file.

```text
# layer1-chains
package: 0.8.0
extras: search extract research answer browser mcp evals langchain aws all
search.backends: nimble tavily you brave exa parallel perplexity linkup webiq keenable deepseek agentcore you-mcp exa-mcp parallel-mcp auto
search.preferred: perplexity nimble linkup tavily brave webiq parallel exa you
search.keyless: tavily you-mcp exa-mcp parallel-mcp keenable
search.appended: keenable deepseek agentcore
extract.backends: nimble tavily exa linkup firecrawl oxylabs brightdata webiq keenable deepseek jina auto max
extract.preferred: nimble tavily firecrawl oxylabs brightdata
extract.keyless: tavily jina firecrawl keenable
extract.appended: linkup exa webiq keenable deepseek
template.backends: nimble
research.backends: nimble parallel perplexity linkup you auto
research.preferred: nimble parallel perplexity linkup you
answer.backends: perplexity parallel linkup you exa
browser.backends: steel browserbase hyperbrowser anchor kernel local auto
browser.preferred: steel browserbase hyperbrowser anchor kernel
browser.keyless: local
browse.backends: browser-use browserbase skyvern auto
browse.preferred: browser-use skyvern browserbase
```

## 6. Calling the package

```python
from web_router import AsyncWebRouter, Provider, WebRouter

router = WebRouter(Provider.NIMBLE, provider_config={"nimble": {"timeout": 30}})
response = router.search("...", max_results=5)
```

`AsyncWebRouter` has the same methods. Provider names are case-insensitive strings or `Provider` members. Credentials come from the environment or from `provider_config`. A key in `provider_config`, Nimble's `async_client`, or `agentcore`'s `gateway_url` counts as held for the default chain, and `auto` builds that member with it.

There is no package index. Install from a clone, as the README describes: `make setup`, then an editable install of the clone. A missing extra's error names `uv sync --extra <name>` in the clone, or `make setup` for every extra.

### Extras

`search`, `extract`, `research`, `answer`, and `browser` are named after methods. `all` installs those plus `aws`.

`aws` is not a method. It installs botocore for backends reached through an AWS account. `agentcore` is the one so far, and the search registry records its extra as `aws`, not `search`. `mcp` (the stdio server), `evals` (judges for `evaluate()`), and `langchain` (LangChain tools) are not methods either. The set is the `extras` line above, and it matches `[project.optional-dependencies]` in `pyproject.toml` and the `EXTRA_*` names in `constants.py`.

`browser` is Playwright for page actions on a handle. Opening a vendor session does not need it. `local` needs a Chromium on the machine. The `setup` target in the Makefile installs it (`uv run playwright install chromium`). `make install` does not.

### Opt-in `decider=`

`classify()` and `execute()` are an opt-in spike. They run only when the caller passes `decider=`. `classify()` returns a `Route` (a method and an `auto` chain) and calls no backend. `execute()` runs that route. Without `decider=`, routing is unchanged: the named provider, or the fixed `auto` order.

The spike is a baseline for measuring decision models. It is not a stable routing contract, it is not Layer 2, and this document does not claim that a decider routes better than the fixed order or better than naming a provider. Decider names and `Route.fallback` are in the README section "Classifying a query before running it (spike)".

The Claude, OpenAI, and LangChain tools do not call the spike. The model supplies the query or the URLs. The provider and the options are fixed when the tools are built. `routing_guide()` is static text. See [AGENTS.md](../../AGENTS.md) and the README integration sections.

## 7. Conformance

The package conforms when all of the following hold.

1. The seven methods exist, each with its own registry, and the built-in names are the `*.backends` lines.
2. `auto` tier membership and order are the `preferred`, `keyless`, and `appended` lines. Runtime filtering is the rule in [section 5](#5-auto-chains).
3. `response.provider`, and a browser handle's provider, name the backend that served the call, never `auto`.
4. An unmapped parameter, an unsupported format, and an unservable value raise, as in [section 4](#4-fail-loud). They are not dropped or swapped.
5. Unset fields are not sent, except the three cases in [section 3](#3-request-and-response-rules).
6. Missing facts stay missing. `outcome` is `partial` only when the backend says the run stopped early.
7. `raw` and `extra` keep what the normalized fields do not.
8. A partial extract stays successful unless every URL failed.
9. A billed chain does not start a second run, session, or task after one member may have accepted the first.
10. `classify()` and `execute()` stay off unless `decider=` is set.
11. `agentcore` is a search backend, its install extra is `aws`, and its place in `search.appended` is the line above.
12. The `package` line equals `web_router.__version__`. A version bump updates that line in the same change.

Public helpers the library also provides: `WebRouter`, `AsyncWebRouter`, `Provider`, `provider_name`, `available_*` and `configured_*` per method (environment variables only for `configured_*`), and `register_*` / `unregister_*` for a backend added outside the package.

## 8. Layer 2 is not built

Layer 2 would own task routing, budget-aware selection, quality targets, cost and latency trade-offs, and freshness requirements. Those parameters are not part of Layer 1.

What exists today measures. `benchmark()`, `benchmark_extract()`, and `evaluate()` do not choose a backend for a later call, and they do not rewrite `auto`'s order. The `decider=` spike picks a method and a chain only when the caller turns it on. Turning it on is not a claim that the choice beats the fixed order.

Layer 1 does not pre-build Layer 2. It keeps one shape per method and fail-loud validation so a later selector can sit on top without a second request model.

## References

- Repository: [WebContextLayer/webrouter](https://github.com/WebContextLayer/webrouter)
- README: install, provider tables, keyless paths, the `decider=` spike
- `AGENTS.md`: rules for changes in this repository
- `CONTRIBUTING.md`: adding a provider, promoting an option
- `docs/mcp.md`: the stdio server
- `examples/`: runnable scripts

## Status and verification

**Package:** 0.8.0. Relative to 0.7.0, 0.8.0 adds `agentcore` and the `aws` extra. `auto` tries `agentcore` last in the search appended tier, so an existing call does not move. The `decider=` spike shipped in 0.7.0 and is unchanged as a contract: off unless the caller opts in, and not part of the default route. This tree also includes `answer` and the `answer` extra, added after the 0.8.0 tag while `web_router.__version__` is still 0.8.0.

**Earlier verification (package 0.0.1, not re-run on 0.8.0):** keyless search and extract passed. Keyed search and extract on several providers passed. `provider="tavily"` without a key raised `ConfigurationError`. `auto` without a key was served by an anonymous tier, and `response.provider` named that backend. There is no shipped `web_router.ai_sdk` module.

**Shipped and not re-verified in this document:** `research`, `answer`, `browse`, `open_browser`, templates, `max`, `agentcore`, the agent integrations, `benchmark()`, `benchmark_extract()`, `evaluate()`, and `classify()` / `execute()`. The README records how far each backend mapping was checked.

**Not built:** adapters in other languages. Layer 2.

**Checked in unit tests, with no network:** the normative block against the registries, `constants.py`, the `EXTRA_*` names, and `pyproject.toml` (`tests/test_layer1_contract.py`). Fail-loud on the public methods stays in the existing contract tests.
