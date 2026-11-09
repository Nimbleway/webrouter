# AGENTS.md

Guidance for AI coding agents working in this repository. Humans: see
[CONTRIBUTING.md](CONTRIBUTING.md), which this file complements rather than replaces.

## What this project is

`web-router` is a Python package that puts one typed interface in front of many
web APIs, across seven methods.

**`search`** — Nimble, Tavily, You.com, Brave, Exa, Parallel, Perplexity,
Linkup, WebIQ, Keenable, DeepSeek and AWS AgentCore Web Search, plus the keyless `you-mcp`, `exa-mcp` and `parallel-mcp`, and `auto`, the default, which
tries the backends you hold keys for and falls back to the keyless ones.

**`extract`** — page contents for a URL you already have: Nimble, Tavily, Exa,
Firecrawl, Linkup, Oxylabs, Bright Data, WebIQ, Keenable, DeepSeek, Browserbase,
the keyless Jina Reader, `auto` again, and `max`, which sends the request to
every member `auto` would try at once and returns the first document back -- the
fastest answer any backend can give, at one fetch per member, so never the
default. `profile` names a saved identity on the backend, the one a browser
session logged into, whose cookies ride along; only Browserbase maps it.

**`live_data`** — a vendor's ready-made scraper for one site (Nimble's
Extract Templates, Apify's Store Actors, Bright Data's scrapers, Oxylabs'
dedicated sources), run for this call and answering with structured
`records` shaped by the source's own output schema rather than page text. Its
inputs are the source's own -- a URL for some, an id, a slug or a search term
for others -- which is why it is a method and not an option on `extract`.
`live_data_sources` lists one page of the vendor's catalogue and
`live_data_source` describes one, schemas and all; the three share one
registry. Nimble, Apify, Bright Data and Oxylabs. No keyless tier and no
`auto`: a run fetches and parses on the backend's account, and a source's
name belongs to one vendor, so `provider` is required. **A backend joins only
if it fetches on request**: data a vendor collected earlier and serves from
storage is that vendor's own API, reached through `call_vendor`, never here.
These were `run_template`, `templates` and `template`; the old names, and the
`Template*` types, are deprecated aliases in `client_live_data.py` and
`live_data_compat.py` until a release removes them.

**`research`** — an agentic run: the backend reasons over several turns and
answers in minutes rather than milliseconds. Nimble, Parallel, Perplexity,
Linkup, You.com, and `auto`. It has no keyless tier, because every backend bills for the run.

**`answer`** — one question, answered in one call: the backend searches and
writes the answer itself, and the response carries it with the sources it rests
on. Between `search`, which returns pages, and `research`, a job of minutes.
Nothing here composes or edits the answer. No keyless tier, since every backend
bills the call, and no `auto` until its order is chosen: with no `provider`,
the router's default serves it when that is an answer backend, else the one
configured answer backend -- a key in `provider_config` counts -- and
otherwise the call raises `ProviderNotFoundError`, naming the configured
backends when there are several and the variables to set when there are none.

**`open_browser`** — a browser session: not an answer but a handle, carrying a
CDP URL the caller drives with Playwright, browser-use or Stagehand, and
released when the block holding it ends. The handle can also drive the page
itself — `goto`, `snapshot`, `click`, `type`, `press`, `screenshot`, `read` —
installed by [browser]. Steel, Browserbase, Hyperbrowser, Anchor, Kernel, the keyless
`local`, which launches a Chromium on this machine, and `auto`, which tries the
vendors you hold keys for and then `local`.

**`browse`** — a hosted browser-agent task: a task goes in, the vendor's agent
drives one of its own browsers through it, and an answer comes out minutes
later. It is research's shape on the same job core, with `submit_browse` for
the job handle. Browser Use, Skyvern, Browserbase, and `auto`. Like research, it
has no keyless tier, because every task bills.

A name does not imply every method. `brave` searches and cannot extract,
`oxylabs` extracts and cannot search, `nimble` searches, extracts, runs
live data and researches through four classes, and `browserbase` opens
sessions, runs browse tasks and extracts through three. Each method has its own registry,
and asking one for another's backend reports it as unknown.

It is pre-1.0 and in alpha. Assume every change is read by people outside the team.

The whole value of the package is that **a call behaves the same whichever
backend serves it**. Changes that leak provider-specific behavior into callers
defeat the point, even when they pass tests.

## Layout

```
src/web_router/
├── types.py            SearchRequest/Response/Result, ExtractRequest/Response/Document,
│                       ResearchRequest/Response/Progress + Citation,
│                       BrowserRequest/Session/State + Viewport,
│                       BrowseRequest/Response/Progress + BrowseStep
│                       — the normalized contract, one request model per method
├── types_decision.py   DecisionRecord + DecisionHop/DecisionAttempt, on every response a
│                       call can route and on Plan; types re-exports it, it imports nothing
├── types_answer.py     AnswerRequest/Response + Recency: answer()'s contract, apart so
│                       types.py stops growing; it imports types, never the reverse
├── types_live_data.py  LiveDataRequest/Response + LiveDataSource/SourcePage/Sample:
│                       live data's contract, apart on the same terms
├── types_page.py       PageState/PageSnapshot/Screenshot + PageLoadState/PageFormat:
│                       what a browser handle's page actions return; imports nothing of ours
├── types_benchmark.py BenchmarkRun/Report + ExtractBenchmarkRun/Report: what benchmark()
│                       and benchmark_extract() return; it imports types, never the reverse
├── client.py           WebRouter, AsyncWebRouter — the public facade
│                       (search, extract,
│                        answer, research + submit_research,
│                        browse + submit_browse, open_browser, benchmark and
│                        benchmark_extract, fan-outs over search and extract,
│                        and evaluate, accuracy on top of benchmark)
├── client_answer.py    answer() and get_answer_provider(), mixed into both routers
├── client_live_data.py live_data(), live_data_sources(), live_data_source() and the
│                       deprecated template names, mixed into both routers
├── live_data_compat.py the old Template* names, importable from web_router with a warning
├── client_plan.py      plan() and run(), prompt-level routing, mixed into both routers
├── client_benchmark.py benchmark(), benchmark_extract() and evaluate(), mixed into
│                       both routers
├── client_budget.py    budget(), mixed into both routers
├── client_scope.py     the BudgetScope / AsyncBudgetScope budget() returns
├── client_requests.py  the routers' request builders: a method's arguments in, its
│                       normalized request out; JsonSchema; the sync router's
│                       async_client check
├── evals/              accuracy: a question set, judged — installed by [evals]
│   ├── types.py        EvaluationReport / EvaluationRow / ProviderScore, Grade
│   ├── datasets.py     Dataset / Question — SimpleQA, SimpleQA Verified, FRAMES,
│   │                   SealQA, or a CSV; seeded sampling; answer-key pages withheld
│   ├── judge.py        Judge ABC, OpenAIJudge and AnthropicJudge: synthesize,
│   │                   then grade
│   ├── run.py          the loop: benchmark per question, judge per row, score
│   └── prompts/        the SimpleQA grader prompt, vendored verbatim
├── constants.py        shared env var registries, timeouts and message templates;
│                       a provider's own go in constants_<provider>.py (older
│                       providers' are still here)
├── constants_answer.py answer()'s messages and MCP tool text, imported from here by name
├── constants_live_data.py live data's messages, MCP tools, builtin providers and env table
├── constants_apify.py  Apify's paths; constants_live_data_vendors.py Bright Data's and Oxylabs'
├── constants_you.py    You.com research's endpoints, efforts and status table,
│                       kept apart so constants.py stops growing
├── constants_deepseek.py DeepSeek's endpoints, limits and query operators,
│                       kept apart for the same reason
├── constants_kernel.py Kernel's endpoints, field table and session states,
│                       kept apart for the same reason
├── constants_browserbase.py Browserbase's host, header, session, browse and Fetch
│                       settings, kept apart for the same reason
├── prices.py           estimated list prices per (provider, method, mode), read
│                       from data/prices.json; PRICING.md's tables are made from it
├── prices_latency.py   the check a row's measured latency passes, apart from
│                       prices.py so it stays small; it never imports Price
├── data/prices.json    the price list itself (experimental), shipped in the wheel
├── data/chains.json    search's and answer's default `auto` orders (`make chains`)
├── data/efforts.json   each research effort's accuracy and p90 (`make efforts`)
├── _chains.py          reads data/chains.json: search's and answer's default
│                       `auto` orders, generated by `make chains`, never by hand
├── constants_routing.py routing's candidate-id separator and messages
├── constants_budget.py a budget's skip reasons and BudgetExceededError's message
├── constants_candidates.py the candidate builder's getters and drop reasons,
│                       kept apart so constants_routing.py stays small
├── constants_invoke.py a router's default timeout and messages
├── routing/            the router contract (experimental): what a router is shown
│   │                   and what it may answer, one module per part
│   ├── contract.py     ToolName, Budget, Candidate, RouteRequest, RouteDecision, Plan
│   ├── attempts.py     the seam in auto's fallback loops: a per-call order and
│   │                   a hook before each attempt, held in a context variable
│   ├── budget.py       budget_scope(), which every budget= call runs in: its deadline,
│   │                   the check before each attempt, BudgetExceededError, the record
│   ├── ledger.py       one budgeted or routed call's ledger: what each attempt is charged,
│   │                   the spend, the position that ran
│   ├── trail.py        the hop trail: every candidate a call moved past, skipped,
│   │                   failed or empty, by attempt
│   ├── pool.py         a shared budget: reservations under one lock, its clock, status()
│   ├── pool_bind.py    which shared budgets are in force here: bind, unbind, current_pool
│   ├── pool_status.py  BudgetStatus, where a shared budget stands
│   ├── record.py       the record's own fields: decision_id, the prompt's hash, a failure's code
│   ├── pricing.py      the one cost and p90 rule: price list, floor, provider_options and
│   │                   results past a row's price unknown, research's p90 from the effort table
│   ├── invoke.py       router=: the two forms a router takes, called sync or async on
│   │                   its own thread or loop within its timeout, and the failure policy
│   ├── _runner.py      the worker thread and private loop a router runs on, the wait
│   │                   within its deadline, and the cleanup of what it leaves behind
│   ├── _forms.py       a router's sync and async sides and its declared timeout_s
│   ├── routed.py       the routers' mixin every routable call runs through: whether
│   │                   to ask the router (route_tools, auto only), its candidates, and
│   │                   the budget's scope around it
│   ├── submitted.py    submitted(): a job's start (submit_research, submit_browse)
│   │                   held to the shared budget in force, at the effort it maps
│   ├── decide.py       decided(): candidates and a router's outcome merged into the
│   │                   method, order, options and record a call runs with, pure
│   ├── params.py       map_effort(): the research effort a budget picks, from
│   │                   the effort table and the price list
│   ├── effort_table.py reads data/efforts.json (`make efforts`): measured
│   │                   efforts, the noise floor, an effort's p90
│   ├── candidates.py   build_candidates(): every (tool, provider) a call's `auto`
│   │                   chains can serve, priced, timed and prefiltered by the budget
│   ├── candidates_priced.py one method's members priced, timed, and dropped when the
│   │                   budget leaves them out
│   ├── effort_call.py  choose_effort(): the one research effort a call sends, chosen
│   │                   before its candidates are priced or a router is asked; a call
│   │                   no router orders runs at it too
│   ├── eligible.py     eligible_members(): the members of a method's `auto` that
│   │                   can serve a request, and the ones it cannot build, with why
│   ├── merge.py        merge(): a router's decision and the static order made into
│   │                   one method, one order and checked options, pure
│   ├── merge_types.py  Merged, MergeNotes and FallbackReason: what the merge answers
│   ├── merge_shape.py  whether a router's answer is a well-formed decision that
│   │                   decides something
│   ├── merge_params.py the merge's check on a router's options: allowlist, values,
│   │                   the caller's own, the budget, the URL policy
│   ├── merge_extract.py rule 7: extract with no URL left to read searches instead,
│   │                   when a search backend is offered
│   ├── merge_urls.py   a router's words with every URL cut to its scheme and host
│   ├── url_policy.py   url_policy: the URLs a plan or a router may send, a hostname
│   │                   check before dispatch (no DNS, no redirects)
│   └── rules.py        rules(): the built-in keyless router, URL to extract, else search
├── constants_params.py the effort mapping's data path, rule and reasons
├── constants_merge.py  the options a router may set, and the merge's reasons and notes
├── constants_mcp.py    the MCP server's instructions, and its routing tools' names, schemas,
│                       descriptions, environment variables and messages
├── constants_mcp_hints.py each MCP tool's title and behaviour hints
├── _urls.py            urls_in(): the URLs written in a query's prose; host_of(): a
│                       URL shown as its scheme and host only
├── _hosts.py           a URL host's kind -- loopback, cloud metadata, private -- in
│                       the spellings a browser reads; shared by the MCP navigation
│                       guard and url_policy
├── constants_urls.py   the punctuation stripped from a URL found in prose, and the
│                       metadata, loopback and legacy-IPv4 host lists
├── constants_prices.py the price list's data path, unit divisors, messages and
│                       PRICING.md wording, kept apart for the same reason
├── registry.py         name → provider class, one table per method, lazily
│                       imported; available vs configured
├── registry_answer.py  the answer backends' table, and the backend an
│                       unnamed answer() uses
├── exceptions.py       WebRouterError and subclasses
├── mcp/                the stdio MCP server — a thin projection of the methods
│                       for an agent to call, installed by [mcp]
│   ├── methods.py      the library method behind each provider-picking tool: its
│   │                   registry, classes, options and env table, in one table
│   ├── schema.py       a tool's schema narrowed to the backends serving it here,
│   │                   its declared arguments and its behaviour hints
│   ├── env_credentials.py which variables are provider credentials, and how a
│   │                   file's values join the environment
│   ├── messages.py     a failed tool call's message: provider_config swapped for
│   │                   load_env, then redacted
│   ├── urls.py         public_only on the URLs extract, browse and live_data
│   │                   are given, refused before anything is sent
│   ├── routing_config.py the operator's router and ceiling, read once at startup
│   ├── routing.py      web_run: plan a prompt with that router, run it, a job as a handle
│   ├── budget.py       one connection's budget: the ceiling, budget_start's task, and
│   │                   the charge for live_data and browser_open
│   ├── live_data_tools.py  the three live data tools; shaping_live_data.py shapes them
│   └── chains.py       the members a delegating provider would try, per tool
├── integrations/       agent-framework adapters, one module each
│   ├── _render.py      what a model reads back, one layout for every framework
│   ├── _options.py     which backend each tool calls, fixed when it is built
│   ├── _tools.py       the Claude and OpenAI tools' shared half: names,
│   │                   schemas, input checks, the executor, routing_guide()
│   ├── langchain.py    WebRouterSearch / WebRouterExtract tools — installed by
│   │                   [langchain]
│   ├── anthropic.py    WebRouterTools (Claude custom tools + local executor)
│   │                   and routing_guide() — no SDK, no extra
│   └── openai.py       WebRouterTools (OpenAI function tools, Responses and
│                       Chat Completions, + local executor) — no SDK, no extra
└── providers/
    ├── base.py         SearchProvider / ExtractProvider / LiveDataProvider /
    │                   ResearchProvider / BrowseProvider /
    │                   BrowserProvider ABCs over a shared
    │                   base; the generic JobProvider / Job core that
    │                   ResearchProvider / ResearchJob and BrowseProvider /
    │                   BrowseJob specialize; BrowserHandle / AsyncBrowserHandle
    ├── answer.py       AnswerProvider, answer()'s base, and the helpers its backends share
    ├── _http.py        HttpSearchProvider / HttpExtractProvider /
    │                   HttpResearchProvider / HttpAnswerProvider / HttpBrowseProvider /
    │                   HttpBrowserProvider — for APIs with no SDK
    ├── _utils.py       payload assembly, key and setting resolution, dict
    │                   coercion, body selection
    ├── _search_text.py a body cut to a snippet; domain filters as site:
    │                   operators, and the domain lists a backend refuses
    ├── _deadline.py    the per-call deadline, its budgets, and the mixin
    │                   that holds a provider's call to it
    ├── _clamp.py       a transport's own timeout, clamped to the deadline's time left
    ├── _page.py        the page actions both browser handles delegate to,
    │                   once over CDP through Playwright; snapshot refs
    ├── _png.py         a screenshot as PNG: converts what a backend returns as
    │                   another format (Anchor: JPEG), and reads the size
    ├── nimble.py       search + extract + research, via the nimble-python SDK;
    │                   the shared clients and constructor of the four Nimble classes
    ├── nimble_live_data.py  Nimble's templates as live data, on nimble.py's clients
    ├── _http_live_data.py   HttpLiveDataProvider: the base of the three below
    ├── apify.py        live data: Apify Store Actors, run synchronously
    ├── brightdata_live_data.py  live data: Bright Data's scrapers, one input per run
    ├── oxylabs_live_data.py     live data: Oxylabs' dedicated sources, parsed
    ├── tavily.py       search, via the tavily-python SDK; re-exports tavily_extract.py
    ├── tavily_extract.py  extract, and the SDK clients, key and timeout both classes share
    ├── exa.py          search + extract, via httpx (its research API is retired);
    │                   re-exports exa_answer.py
    ├── exa_answer.py   answer, via httpx
    ├── you.py          search, via httpx — You.com publishes no Python SDK; re-exports
    │                   you_research.py and you_answer.py
    ├── you_research.py research, a polled background task
    ├── you_answer.py   answer, in one call
    ├── _mcp.py         the keyless MCP search transport: one JSON-RPC tools/call
    ├── you_mcp.py      You.com's keyless MCP endpoint; subclasses you.py
    ├── exa_mcp.py      Exa's keyless MCP server; subclasses exa.py, reads its text
    ├── parallel_mcp.py Parallel's keyless Search MCP; subclasses parallel_search.py
    ├── max.py          extract's parallel chain: auto's members all at once,
    │                   the first document wins, with an optional hedge
    ├── _race.py        max's bookkeeping: the calls of one race, sync or async,
    │                   handed back in the order they finished
    ├── _chain.py       what every auto chain is made of: resolving the members,
    │                   the caller's `chain`, what a member's failure means
    ├── _chain_deadline.py a one-call chain out of time: the first empty
    │                   answer it holds, else the deadline naming each reason
    ├── _auto_extract.py which members of extract `auto` can serve a request: the
    │                   format and every option it sets; apart so auto.py stops growing
    ├── auto.py         the default for search, extract, research, browser and
    │                   browse: keyed backends, then keyless (research and browse
    │                   have no keyless tier, so their chains end there; a browser
    │                   chain ends with `local`)
    ├── auto_browser.py the browser chain's `auto`, apart so auto.py stops growing;
    │                   auto.py re-exports it
    ├── brave.py        search, via httpx; re-exports brave_answer.py
    ├── brave_answer.py answer, streamed, via httpx
    ├── parallel.py     research + answer, via httpx; re-exports parallel_search.py
    ├── parallel_search.py  search, via httpx
    ├── perplexity.py   search + research, via httpx; re-exports perplexity_answer.py
    ├── perplexity_answer.py  answer, and the answer text and sources research reads
    ├── linkup.py       search + extract + research, via httpx
    ├── webiq.py        search, via httpx — Microsoft AI's Web Search API
    ├── keenable.py     search, via httpx — keyless on its /public twins;
    │                   re-exports keenable_extract.py
    ├── keenable_extract.py  extract, and the keyless switch both classes share
    ├── deepseek.py     search + extract, via httpx — filters ride in the query
    ├── agentcore.py    search, MCP via the caller's AgentCore Gateway: SigV4 (botocore, `aws` extra)
    ├── agentcore_sigv4.py  the SigV4 signing agentcore.py's requests go through
    ├── firecrawl.py    extract, via httpx
    ├── oxylabs.py      extract, via httpx — HTTP Basic, username + password
    ├── brightdata.py   extract, via httpx — needs a zone beside its token
    ├── jina.py         extract, keyless — Jina Reader
    ├── steel.py        browser, via httpx — the CDP URL carries the key
    ├── browserbase.py  browser + browse + extract, via httpx — Sessions, Agents
    │                   and Fetch APIs; one Context serves `profile` on sessions
    │                   and Fetch (browse tasks do not map it)
    ├── hyperbrowser.py browser, via httpx — stops with a PUT
    ├── anchor.py       browser, via httpx — timeouts in whole minutes
    ├── kernel.py       browser, via httpx — an idle limit and no lifetime
    ├── local.py        browser, keyless — a Chromium subprocess on this machine
    ├── browser_use.py  browse, via httpx — Browser Use Cloud's v2 tasks
    └── skyvern.py      browse, via httpx — tasks on Skyvern Cloud
tests/                  mirrors src/; tests/providers/ holds per-provider tests,
                        tests/mcp/ the server, tests/max_extract/ the race
                        (its conftest holds the fake members), tests/budget/
                        the per-call and shared budgets (its conftest the priced chain).
                        routing_helpers.py holds the router tests' fake members,
                        wr(), Ranker and the tool, called and priced fixtures.
                        test_contract.py, test_extract_contract.py, test_live_data_contract.py,
                        test_research_contract.py, test_answer_contract.py,
                        test_browse_contract.py and
                        test_browser_contract.py guard the seven shared
                        contracts; test_job_core.py the job core under
                        research and browse; test_deadline_*.py the
                        per-call deadline on every method, over the fakes
                        in deadline_helpers.py;
                        test_browser_actions.py the page actions, against
                        fixtures/browser/; test_auto_browser.py and
                        test_auto_browse.py the two billed `auto` chains;
                        providers/test_http_base.py guards the HTTP plumbing,
                        providers/_browser_live.py is the live browser
                        tests' shared check, and test_shape.py the file
                        length limit, against shape_baseline.json
examples/               runnable scripts, indexed by examples/README.md;
                        router_*.py the example routers (rules, a
                        scikit-learn classifier, an OpenAI-compatible
                        model, Jev), tested offline in tests/examples/
benchmarks/agent/       one agent over FRAMES on its platform's built-in web
                        search and on web-router — Claude on Managed Agents or
                        OpenAI on the Responses API; a script, not a method;
                        run with --group agent-bench
benchmarks/providers/   run.py: every search backend on the same SimpleQA
                        questions through evaluate(), by topic and answer
                        type; extract.py: every extract backend on the same
                        URLs through benchmark_extract(), by category, with
                        the URL sets under urls/; answer.py: every answer
                        backend on the same questions, each backend's own
                        answer graded, beside a search scan; frozen.py and
                        frozen/: the committed per-question results;
                        route_eval.py: a router replayed over them and
                        scored against the static order, its ranking in
                        route_eval_rank.py, tool and effort paths in
                        route_eval_paths.py, a decision merged by the
                        library's merge rule in route_eval_merge.py, one
                        question walked in route_eval_replay.py, its
                        command line in route_eval_cli.py; chains.py (`make chains`): the
                        default auto orders ranked from them into
                        data/chains.json; efforts.py (`make efforts`): each
                        research effort's accuracy and p90 into
                        data/efforts.json, both written or checked by
                        _emit.py — scripts; each
                        run's report stays under results/
```

## Commands

```bash
make install   # uv venv + all extras + dev deps + pre-commit hooks
make check     # lint + typecheck + unit tests — run before declaring done
make format    # ruff format and autofix
make test      # unit tests only, no network
make test-browser  # tests that launch a real local Chromium; skip without one
```

Cloud Agent VMs already set `core.hooksPath`, so `pre-commit install` exits
rather than replacing those hooks. Leave `core.hooksPath` as it is and run
`uv run pre-commit run --all-files` when the hooks themselves should run.

A `local` browser needs Chromium. `make install` does not install it.
`make setup` does (`uv run playwright install chromium`).

Use `uv run <cmd>`, never a bare `python` or `pip`. Dependencies live in
`pyproject.toml`; after editing it run `make lock && make requirements` and
commit `uv.lock` and the regenerated `requirements*.txt` together. Never
hand-edit `requirements.txt`.

## Rules that matter here

**Never invent data a provider did not return.** If a backend has no relevance
score, `score` stays `None`. Do not synthesize, estimate, or backfill.

**Never silently drop a caller's parameter.** Each provider declares
`supported_parameters`; anything outside it raises `UnsupportedParameterError`.
A search that quietly ignores `country="US"` is worse than one that fails. If a
provider gains support for a parameter, add it to that set — do not special-case
it at the call site.

**`benchmark()` is a consumer of `search()`, not a sixth method, and
`benchmark_extract()` the same consumer of `extract()`.** Each fans one
request out across several providers through the same public path a caller
would use — validation, wrapping and normalization included — so what is
measured is what a caller gets. Neither has a registry of its own and no
provider implements anything for them. A `BenchmarkReport` holds one
`BenchmarkRun` per provider, in the order asked, and an `ExtractBenchmarkReport`
one `ExtractBenchmarkRun`; the two pairs share a base in `types.py`, and the
threads, the deadline and the rows that outlive it live once in `client.py`'s
`_fan_out` / `_afan_out`, so a change to how a comparison runs is made there
for both. A row whose call raised —
a refused option or format, a missing key, a backend failure, or
`BenchmarkTimeoutError` when the comparison's single `timeout` ran out —
carries that error, and the report is returned regardless. One backend failing
never fails the comparison, exactly as a partly failed extract stays partly
successful — and an extract row that fetched some URLs and not others is an
answered row whose response says which, never an error. A row carries
measured facts only: latency around the call, the result or document count,
the full response. Never add a quality score, a relevance estimate, a
completeness figure or a cost the backend did not state — that is the "never
invent data" rule applied to metadata about the call. `search` and `extract`
only: `research`, `browse` and `open_browser` bill per run or per minute, so
comparing them is a decision the caller makes explicitly per provider, not
something a comparison method starts by default. `auto` is left out of both
default sets (`BENCHMARK_EXCLUDED`) because it would repeat one of the others
under a name that says nothing about who answered. `format` is one value sent
to every extract row, as `search_depth` is to every search row; unset, it is
the one request every backend can serve.

**`evaluate()` is the one place a judgment enters a report, and it is
labelled.** Accuracy is an LLM's grade of an answer the same LLM synthesized
from a provider's results — one fixed judge over every backend, so the figures
read the same way — and the `EvaluationReport` names that judge, because
the number is comparable only with figures the same judge produced. It is built
on `benchmark()`: one fan-out per question, then two judge calls per row the
backend served, and nothing talks to a backend outside that path. The grader
prompt is `openai/simple-evals`' SimpleQA prompt vendored byte-for-byte at
`evals/prompts/simpleqa_grader.txt`; never edit it — re-vendor from upstream —
and never send a search provider's own vocabulary into it. The denominators are
fixed: `accuracy` is `correct / served`, so a served question whose answer was
`not_attempted` is a miss, while `accuracy_when_attempted` is
`correct / (correct + incorrect)`, upstream's figure; a row that failed at the
search or at either judge call is excluded from both and counted in
`failure_rate`, with `failed_at` saying where. A judge that cannot run — no SDK,
no key — raises before the first search is paid for. `auto` is excluded as it
is from `benchmark()`. No other method may call a judge, and no report outside
`evals/` may carry a score: `benchmark()` stays measured facts only, and a
provider never grades itself. `search_depth` and `max_results` are one value
sent to every provider in a call — that is what makes the rows comparable — so
a comparison of vendor tiers is one `evaluate()` per depth on the same dataset,
never a per-provider override.
A `Dataset` is a pydantic model and iterates as its fields: loop over
`.questions`.

**An integration fixes the router's options when the tool is built.** The
LangChain, Claude and OpenAI tools take the query or the URLs from the model
and nothing else; the provider, `max_results`, `search_depth` and the filters
are the caller's, sent unchanged on every call, so an agent does not behave
differently depending on who served it. Do not widen a tool's `args_schema` with an option
some provider refuses. A `ProviderError`, and input the model got wrong,
reach the model as a tool error it can react to; every other error is the
caller's setup and raises. The model reads `integrations/_render.py`'s
rendering — one layout for every backend and every framework, cut to a budget —
and the untouched response stays with the caller. An integration imports its
framework in its own module only — `import web_router` never pulls it in — and
the Claude and OpenAI ones import no SDK at all: definitions are data, the
caller's client sends them. Those two share everything but the envelope —
names, schemas, input checks, the executor and the routing guide live in
`integrations/_tools.py` — so a change to what the model is given goes there,
never into one of the two. Their tool names never collide with Claude's
built-in `web_search` / `web_fetch` or OpenAI's built-in `web_search`. The
OpenAI definitions are strict, and strict mode answers a 400 for a JSON Schema
keyword outside its subset, so a keyword added to the shared schemas must be
one it permits: `OPENAI_STRICT_TOOLS` records what was checked live, and a
unit test holds the schemas to it. OpenAI's tool output has no error flag, so
every error the model reads says in its text that the call failed or did not
run. Add research or browse to them only with a routing-guide paragraph saying
when each is worth its bill.

`benchmarks/agent/` is a consumer of the library, like `examples/`: it
drives Claude agents on Managed Agents through the Claude tools, OpenAI
models on the Responses API through the OpenAI tools, or both in one run —
each arm compared with its own platform's native search, since the two run
different models — and grades with an
`evaluate()` judge — by default from the other family, `OpenAIJudge` for
Claude and `AnthropicJudge` for OpenAI, so no arm is graded by its own.
`--judge` picks either for either platform; a run of both needs one judge for
every arm and defaults to `OpenAIJudge`. An `mcp:<vendor>` arm attaches the vendor's own hosted MCP server
instead — its search tool alone, called by the platform — as the control for
what web-router adds over connecting that vendor directly; it is the one arm
whose key leaves this machine, to a Managed Agents vault on Claude and a
request header on OpenAI, and neither the key nor anything derived from it is
written to `.managed-agents.json` or the results. A `direct:<vendor>` arm is
the control for a vendor with no hosted MCP server: the vendor's own REST API as
two plain tools, run on this machine, so its key goes only to the vendor. On a
set that withholds answer-key pages, every arm whose tools run here drops them
from searches and refuses to read them, as `evaluate()` keeps them from its
judge; a platform-run arm cannot be filtered, so the pages it showed are counted
and named in the report. When the judge shares an agent's family
the report's setup names that platform, because that judge is not independent
of what it grades. Its winners-by-axis verdicts come from a paired bootstrap
over the rows; they say whether a gap is noise, not how good an answer is. It adds no
method and no report type to the package. Its `report.md` is one of the three
places outside `evals/` a judged accuracy appears, and it names the judge the
same way. The only cost it reports is Anthropic's own list-cost figure for a Claude
session, never an estimate, and it says that a web-router backend's charges
are not in it; OpenAI states no cost, so an OpenAI run shows none. On Claude
it creates its environment and one agent per arm once, recording their ids in
the gitignored `.managed-agents.json`, and updates an agent to a new version
when its arm changes; it never archives either. On OpenAI it creates nothing,
and `--max-turns` stands in for the session budget OpenAI does not have.

`benchmarks/providers/` is the other: a consumer too, it runs every configured
search backend through `evaluate()` on the same SimpleQA questions and breaks
the judged accuracy down by SimpleQA's topic and answer type — the evidence a
routing rule or the routing guide's advice should rest on. Its `report.md`
names the judge and has no cost column. Its recommended order alone reads list
prices from `web_router.prices`, as estimates, to break ties. That order is
route-eval's ranking (`route_eval_rank.py`), so the repository suggests only one
order. `route_eval.py` replays a router over the frozen results offline and
scores it against the static order through the library's own merge rule
(`web_router.routing.merge`), held out and in-sample, against each
method's noise floor (its own rerun's, else search's twin run) and ranks a
backend only on 20 counted questions or more; an unknown price stays unknown there, never $0. `--combine` merges scans only when
their questions, search size and judge match. Keep a scan's report to compare
the next one against: only runs graded by the same judge compare. `extract.py`
beside it is the extract scan: every configured extract backend through
`benchmark_extract()` on the same URLs, one fan-out per URL, tabulated per
backend and per category. It carries no judge and no score — a page is
*returned* when a document came back and *with text* when that document had
any, and a bot wall with text counts, which its report says — so it ranks who
brings a page back, not whose page is best; scoring text against ground truth
is work for a scorer that carries it, which this repository does not. Its URL
sets under `urls/` are `url,category` lists only, no ground truth, and
`--combine` merges scans only of the same file, format and timeout. A setup may name a normalized
depth (`tavily@deep`, one `evaluate()` per depth) or a vendor mode from
`modes.json` (`tavily:ultra-fast`), sent through `provider_options` by a
wrapper that calls the backend's public `asearch`; mark a mode `verified` only
after a live call shows it changing the response. `answer.py` is the answer scan: every configured answer backend through the
public `answer()` on the same questions, each backend's own answer graded by the
same judge as the search scan -- nothing synthesized, since the backend wrote the answer
-- so its `report.md` is a third place a judged accuracy appears, and it names
the judge. `--baseline` sets a search scan of the same questions, seed and judge
beside it, and is refused otherwise. A row whose sources include one of the
set's withheld pages, cited or only read, is *leaked*, never graded and out of accuracy, because an answer backend
has read the answer key before anything could be withheld. It shows a cost only
where the backend's response states one, never an estimate. `PRICING.md` beside it is a
dated, sourced reference for reading results against list prices. Its tables
are generated from `src/web_router/data/prices.json`, which `web_router.prices`
reads to estimate a call's cost before it runs: edit the JSON, never a table,
and run `make prices`; its prose is hand-written. A price comes only from the
vendor's own page, cited in `source_url` and dated in `checked`; one the vendor
does not publish is `unknown` and one billed by tokens is `token_billed`, never
estimated. The library still never puts a price in a response outside
`DecisionRecord`, where an estimate says so in `cost_is_estimate`.

**`constants.py` imports `types.py`, so `types.py` imports nothing of ours**
but `types_decision`, a leaf that imports nothing of ours either.
The import graph is `types` ← `constants` ← `exceptions` ← everything else. A
model that needs an exception type or a message template cannot live in
`types.py` — put it in a subpackage's own `types.py` (as `evals/types.py`
does) or annotate the field as `Exception` and say why in its docstring (as
`BenchmarkRun.error` does). Do not "fix" this by importing lazily inside a
validator. A `constants_<provider>.py` sits at the same level as `constants`:
it may import `types` (and `types_answer`, which imports only `types` and
`types_decision`, and `types_page` and `types_decision`, which import nothing
of ours) and nothing else of ours.

**Never override the public method in a provider.** `search`/`asearch`,
`extract`/`aextract`, `run`/`live_data_sources`/`live_data_source`, `answer`/`aanswer`,
`submit`/`research`/`browse`/`poll`/`cancel` and
`open`/`release`/`status` (each with its `a` twin) live on the base
classes and enforce validation, error wrapping, and — for extract — the URL
fan-out, for research and browse the whole wait-and-poll cycle, and for a
browser the idempotent release. Providers implement `_search`, `_asearch`, `_extract`,
`_aextract`, `_answer`, `_aanswer`, `_run`, `_arun`, `_list`, `_alist`, `_get`, `_aget`, `_start`,
`_astart`, `_fetch`, `_afetch`, `_open`, `_aopen`,
`_release`, `_arelease`, which are pure translation. The seven contract test
modules enforce this.

**A job is the base class's problem.** Research is a job, browse is another,
and so is any method whose backend carries on after the call that started it.
`JobProvider` and `Job` own starting a run, backing off while it polls, timing
out, cancelling, and turning a finished run into an answer or a reason there is
none; `ResearchProvider`/`ResearchJob` and `BrowseProvider`/`BrowseJob` only
put their method's types, messages, timeout error and default wait on them.
A new job-shaped method does the same
rather than copying the cycle, and tunes its wait and poll cadence through the
class attributes on its `Job`, not by overriding. A provider translates two
things: how its API starts a run, and how its API reports one. Do not put a poll
loop, a sleep or a timeout in a provider. A backend that answers inside the
request that started it sets `resumable = False`, returns a completed progress
from `_start`, and implements nothing else.

**A call's deadline reaches the backend through a context variable.**
`research(timeout=...)` and `browse(timeout=...)`, like any job-shaped call, set
it before the run is submitted, not after, and `call_deadline(seconds)` in
`providers/_deadline.py` sets one around any call; every HTTP and SDK call inside
clamps its own timeout down to whatever is left — never up. It is a
`ContextVar` rather than a parameter on `_search`/`_start`/`_fetch` because the
budget belongs to the *call*, not to the request, and would otherwise have to be
threaded through every provider signature between the router and httpx. It is
the one implicit thing in this package; read it through `job_budget(default)`
(`research_budget` is the same function under the name the research providers
already read) or `current_deadline()`, never directly, and a block inside
another only ever tightens it. A one-call method — search, extract, a live data
run, answer — refuses to start past it. The base class mixes in `_bounded` and
`_abounded` from `_deadline.py`; they raise `DeadlineExceededError` when the
clamp cut a call off, and cancel an async call at the deadline. An extract keeps
the documents that came back in time. A transport that sets its own timeout — the HTTP base's
`_clamped()`, Nimble's `_within_deadline()`, Tavily's per-call `timeout` — sends
nothing new when no deadline is in force, and clamps through `call_budget`, whose
floor is a millisecond rather than a job's half second, so it is never handed
more than the time left. `auto` holds nothing back for later
members: each runs under its own timeout clamped to what is left, and none
starts once nothing is. A billed submit is clamped as on its own and never cut
shorter for a member after it. A worker thread does not inherit the variable,
so whoever hands a call to one runs it in `contextvars.copy_context()`.

**Translate a backend's run status through a table, never by hand.** Each job
provider declares its own status table in `constants_<provider>.py` and reads it
through `JobProvider.state_of` (a browser provider through `BrowserProvider.state_of`,
which shares its translation), which raises on a status the table does not list.
Older providers' status tables are still in `constants.py`.
An unrecognized status means the backend changed under us, and treating it as
"still running" would poll a finished job until the timeout.

**`auto` routes around a member that cannot serve the request.** Every chain
offers a wider surface than its weakest member, so a member is checked against
the request before it is tried, on the class, which costs no credential, sends
nothing and starts no billed run. Narrowing the shared surface to the weakest
member instead would cost the others a real capability. Search `auto` declares
every shared option — `AUTO_SUPPORTED_PARAMETERS`, which a test holds at least
the union of its members' — and passes over a member that does not map an
option the request set, or whose `refused_values` refuses the value: Linkup for
`search_depth`, Perplexity and Brave for `search_depth="deep"`. A router's
default option is held to the same two checks on every backend in the order
(`routing/merge_params.py`). Extract `auto`
declares the union of their `supported_formats` and passes over a member that
cannot return the format asked for, the pinned `markdown` default included, so
Exa serves only `text`; it checks `supported_parameters` too, so `profile`
passes over every member but Browserbase, and `max` races only the members that
pass. With no member left, search raises
`UnsupportedParameterError` and extract `UnsupportedFormatError` (`UnsupportedParameterError` for an
option no member maps), each naming
what every member lacked; among the members that can serve the call, a failure
falls through as it always has, and so does an empty answer (no results and no
answer, or every document blank) unless `fallback_on_empty=False`; when every
member is empty the first empty answer is returned. A member whose extra is not installed
cannot be read, so it is kept and fails that way. The MCP server reads search,
extract and browser `auto` as the chain it would try, so a union never
advertises an option no reachable member maps. Its research chain passes over
Perplexity and You.com for `output_schema`, which Nimble, Parallel and Linkup map. Its
browser chain does the
same over every option: `auto(stealth="low")` passes over `local`, and
`idle_timeout` over every vendor without an idle limit. It checks values as
well as names, through `refused_values` and `unmet_requirements` on the
member's class: a stealth level outside its `stealth_levels`, a country outside
its `proxy_countries`, or an option whose partner its `co_requirements` names
but the request left unset (Anchor's stealth without `proxy_country`). The `BROWSER_TIMEOUT`
default is not a caller option and does not count — the member applies it for
itself — and `provider_options` is refused, because it is one backend's own
fields and `local` would read it as Chrome switches. Its browse chain checks
an option outside the member's `supported_parameters` (Browserbase refuses
`start_url`) and a required one the request left unset, through
`missing_parameters`; with no member left it raises `MissingParameterError` or
`UnsupportedParameterError` naming why each was skipped.

**`max` is `auto`'s members raced, and nothing more.** It subclasses
`AutoExtractProvider` for its chain, its format routing and its member
construction, and the router hands it the `chain` and `members_config` set
under `auto` unless its own entry sets them (`MAX_INHERITS_FROM_AUTO`), so
the two can never disagree about who is tried; it differs
only in starting every eligible member at once and returning the first
`ExtractResponse` that came back. Only a document wins: a `_RECOVERABLE`
failure is that member's loss and the race goes on, any other
`WebRouterError` propagates, and with no winner `_exhausted` names each
member's reason under `MAX_AGGREGATE_FAILURE`. It never judges the responses
it did not return -- the first document is the answer, which is "never invent
data" applied to a race. Every fetch a member starts is billed, and most
members take one URL per request, so a call costs up to one fetch per member
per URL; a sync loser keeps fetching the URLs it has not reached after the
winner returned. `hedge` is the one knob: the first eligible member
runs alone for that many seconds, the rest start when it runs out or when
that member fails, whichever comes first; it covers that member's whole call,
so it caps the cost of a one-URL request, not of a list. The whole URL list is one race, so
every document comes from the winner -- and a member whose answer has
`failures` fetched part of the list, so `_Losses` holds it while another
member can still answer and returns the one with the fewest failures only
when none can; a partial answer also starts the hedged rest, as a failure
does. The sync router cannot cancel a loser,
so `close()` waits for the race's stragglers before closing the members; the
async router cancels them. `max` and `auto` are `DELEGATING_PROVIDERS`: the
router hands both every other backend's `provider_config` entry, and
`benchmark()` excludes both. `max` is not a member of `auto` and `auto` is not
a member of `max`.

**Every backend of a method is a member of its `auto`, in a fixed order.**
Search's keyed members (`AUTO_PREFERRED`) and answer's (`AUTO_ANSWER_PREFERRED`)
are generated: `make chains` ranks them with route-eval on the frozen results
and writes `data/chains.json`, and a test fails when that file is stale or the
README's order disagrees with it. All of search's keyed members come before the
keyless tier -- so a key held only for research serves plain searches too, on
purpose. A backend that joins later goes at the end of the file's `appended`
(search; after the keyless tier) or `preferred` (answer), or of
`AUTO_EXTRACT_APPENDED`, and for research after `AUTO_RESEARCH_PREFERRED`'s
original members, so joining never moves a call that was already being served;
`make chains` moves a search or answer member up only once frozen results rank
it. Extract's and research's orders are hand-written: extract's is measured on
pages returned, not graded, and research's is unmeasured. Reordering either is a
PR backed by a provider scan. A caller reorders through `chain`, each member a
name or `(name, kwargs)` -- built with its own `provider_config` entry, those
kwargs over it, so a key passed in code reaches a member as it does a named
backend -- and because `provider_config["auto"]` reaches the
`auto` of all six methods, a list there is every method's chain while a
mapping keyed by method (`AUTO_CHAIN_METHODS`) sets only the ones it names.

**A billed chain falls through only when nothing started.** A session that is
opened is paid for, and so is a task or research run that was accepted, so the
browser, browse and research chains try the next member only on a failure
`_nothing_started` in `auto.py` allows — a missing credential or extra, a 4xx, a connection that
never reached the backend. A 5xx, a read timeout or an unreadable 200 may come
after the vendor created the session or task, so it raises rather than leaving
the first billing while a second one starts; once a member has accepted,
nothing else is started, and `auto` never opens two sessions in one call. The
handle or job `auto` returns is the member's own, so its `provider` names that
member and its release or poll goes to it. Only `auto` overrides
`BrowserProvider._opened`, and a contract test enforces that.

**`local` comes last in the browser chain.** A key in the environment says
which backend the caller wants, as it does for every other method, so the
keyed vendors come first, in the order `AUTO_BROWSER_PREFERRED` records and
explains. `local` is the fallback, never a substitute for a vendor the caller
is paying for.

**Only the backend may say a run was partial.** `ResearchResponse.outcome` is
`"partial"` when the API itself reports that the run stopped early — an
exhausted budget, a truncated generation. A run that simply found little is
`"complete"`. This is the "never invent data" rule in the place it is easiest to
break. `BrowseResponse.outcome` follows the same rule, and in practice every
built-in task backend answers `"complete"`: none reports an early stop that can
be told from a finished run — Skyvern's `timed_out` mixes its step limit with
its clock, and Browser Use's `isSuccess` is its agent's opinion of its answer,
not a report that it stopped.

**Send providers the minimum.** A search provider is called with the query and
the shared options it maps — `max_results`, `search_depth`, `include_domains`,
`exclude_domains`, `country`, `locale`, `recency`, `effort` — and an extract
provider with the URLs and `format`; nothing else, however much the API
accepts. The one kind of value sent unasked is a pin that keeps a vendor's
default from moving under us (Parallel's `mode=fast`, Linkup's required
`depth`), recorded in the contract's `always_sent`. Do not widen a provider's
mapping as a side effect of another task, and do not add machinery for an
option nobody maps. Mapping an option is its own change, from the vendor's
documentation, with the live check that would confirm it listed. A value the
backend cannot serve is refused through `depth_levels` or `refused_options`
on the class, never sent to be ignored or swapped for a weaker one.

**A format a provider cannot return raises, it is not substituted.** Each
extract provider declares `supported_formats` from its own mapping table —
`auto` is the exception, declaring the union of its chain and sending each call
only to a member that returns the format asked for — and
`default_format`, what the API returns when asked for nothing in particular.
Every provider resolves the format through `ExtractProvider.format_of`: the
format asked for, else `EXTRACT_DEFAULT_FORMAT` (markdown) wherever the provider
supports it, else its own `default_format` (Exa's text). One default for every
backend, rather than each vendor's own, because the same call must not hand an
agent raw HTML from one backend and markdown from another. The provider sends
it wherever its API has a field for it; Oxylabs and Bright Data express HTML by
sending nothing, because each adds a field only to switch away from it. Either
way `ExtractedDocument.format` is true because it was asked for rather than true
because a vendor default has not moved, which is why this is the first of the
three places "unset means unset" is deliberately not applied.

**A browser session's lifetime is never left to the vendor.** When the caller
sets no `timeout`, `BrowserProvider` sends `BROWSER_TIMEOUT` (600 seconds) —
the second of the three exceptions to "unset means unset", because a session nobody released
is billed until the backend ends it, and some backends' own limit is hours. It
is filled in after validation and only for a provider that declares `timeout`:
a backend with an idle limit alone is sent nothing, rather than refusing every
call over an option the caller never set. Do not move this into a provider.

**A CDP URL is a credential, and so is a control URL.** Whoever holds either
drives the browser, and some backends put the account key in the CDP URL.
`BrowserSession.cdp_url` and `BrowserSession.control_url` are `SecretStr`s,
`raw` is kept out of both `repr` and `model_dump()`, and a handle's `repr` names the provider and
session id only. `handle.cdp_url` and `handle.control_url()` return the plain
string, because asking for it by name is the one moment it is needed. The MCP
server returns neither. `live_view_url` is only for a page that grants no
control: a vendor's interactive live view is the control URL even when a query
parameter makes it read-only, because whoever holds it can take that off. A
backend that hands one out only on request implements `_control_url`, so the
request is made when a caller asks, never on every open.

**A saved profile belongs to one backend.** `profile` is the backend's own id
or name for it, and `persist_profile` saves back to that profile, so every
backend that maps it declares `BROWSER_PERSIST_PROFILE_REQUIRES` and refuses
`persist_profile` alone, even where the vendor would make a new profile.
`auto` refuses both, as it refuses `provider_options`. Making, listing and
deleting profiles stays with each vendor.

**Every session is released on every path.** Leaving the `with` block,
an exception inside it, and `router.close()` / `aclose()` — which releases
every handle the router opened that is still open, before closing the providers
whose clients the release needs. Releasing twice is a no-op, and a release
that fails is not an error when the backend then reports the session in one of
the provider's `release_ended_states` — what the caller wanted is already true.
The default is `released`, `expired` and `failed`, because a crashed session
bills no more, and raising on it would fail every `with` exit and every
`router.close()`. A backend whose `failed` also covers a stop that went wrong
narrows the set: Hyperbrowser's `close-error` may still bill, so it declares
`released` and `expired` only, and a failed stop is raised. A handle collected while
still open warns with `ResourceWarning` rather than releasing from `__del__`
over the network.

**Page actions are written once, on the handle, never per vendor.**
`BrowserHandle` / `AsyncBrowserHandle` drive the page over `cdp_url` through
Playwright (`providers/_page.py`); a provider never implements a click or a
screenshot, and a vendor's own screenshot or scrape endpoint is not a shortcut,
because its output would depend on who served the session. The handle connects
once, on its first action, and reuses that connection until `release()` closes
it — before releasing the session, on every path — because several vendors end
a session when its CDP client disconnects. Sync handles on one thread share one
refcounted Playwright, since its sync API cannot start twice on a thread. A
translated error is raised outside the `except` that caught Playwright's, so
the unscrubbed original, CDP URL and all, never rides along on `__context__`. Snapshot refs resolve through
Playwright's undocumented `aria-ref=` engine, which knows only the latest
snapshot of each frame: nothing but `snapshot()` may call `aria_snapshot` on
the handle's page, and a ref used after a newer snapshot, a re-render or a
navigation raises `StaleElementError` rather than acting on whatever is there
now. That dependency is why the `browser` extra pins a tested Playwright minor
and `tests/test_browser_actions.py` checks the engine is still registered;
bump the pin only with `make test-browser` green.

**A browser provider's `provider_options` cannot override a mapped option.**
Build the payload with `build_browser_payload`, from a table in
`constants_<provider>.py` (older providers' are still in `constants.py`) saying
where each mapped option goes in the body. A row is one of three shapes, and
stays data: a path, for a value sent as it is; a `BrowserField`, for one
option that sets several fields (`proxy_country` turning a vendor's proxy on
as well as naming the country) with
`REQUEST_VALUE` marking where the caller's value lands and an optional `case`;
or, for `stealth`, a lookup from each level to the fields it sets. It merges
`provider_options` beneath every field a row produces, at every depth, and
raises `ConfigurationError` when both set one. A list is a single value, never
merged or appended to. Two rows producing the same field is a bug in the table
and raises naming it. A lifetime may be the `BROWSER_TIMEOUT` default the
caller never saw, so letting either side win would silently drop the other.

**A value a backend cannot serve raises; it is never swapped for another.**
`stealth` is a level — `"off"`, `"low"`, `"high"` — because no vendor has more
than two tiers; add a level only when a vendor can tell it apart. A backend
maps each level it has onto its own tiers, several levels onto one where it
has fewer (Anchor's one tier serves both `"low"` and `"high"`), and leaves out
a level it has no field for (Browserbase cannot turn stealth off). Asking for a
level outside `stealth_levels`, or a country outside `proxy_countries`, raises
`UnsupportedParameterError` naming the value, whether the backend was named or
reached through `auto`. It is the extract-format rule applied to a value.

**Over MCP, the server holds the session and the model holds an id.** A model
cannot hold a handle or run a `with` block, so the server keeps every session it
opened, gives the model an opaque id to act through, and releases what is left
when it shuts down. The CDP URL stays on the server's side. `browse` /
`browse_status` are shaped exactly like `research` / `research_status` — submit,
wait `MCP_BROWSE_WAIT`, then hand back a handle — and are the one place
`fetch_raw` is not verbatim: a browse handle's payload, every shaped browse
result and every tool's error message (in `call_tool`, so no path misses it)
go through `shaping.redact`, which replaces session, live-view, debugger and
websocket URLs and credential-bearing values. Its rules are the `MCP_REDACT_*`
tables in `constants.py`; widen them when a vendor's payload shows a new key,
never skip them. The `browser_*` tools address a session by an id
`mcp/browsers.py` mints at random — never the backend's id, which the library's
own `BrowserActionError` message names, so a model-facing error is built from
the error's `detail` through `MCP_MSG_BROWSER_ERRORS`, never from `str(exc)`.
Their every output is redacted too, and each session's CDP URL, its query,
backend id and live-view URL are also replaced literally. The table keeps one
`AsyncBrowserHandle` per session for its whole life, runs one action at a time
on it, releases it after `MCP_BROWSER_IDLE_TIMEOUT` without a call, refuses
past `MCP_BROWSER_MAX_SESSIONS`, and `serve()` releases every session on stdin
EOF and on a shutdown signal — the signal path releases and then exits, because
the SDK's stdin read cannot be cancelled. `browser_open` never exposes
`provider_options` or a profile: do not add them to its schema. Every MCP open
sends a lifetime clamped to `MCP_BROWSER_SESSION_TIMEOUT_LIMIT`, action
timeouts and `max_chars` are clamped in the handlers as well as capped in the
schemas, and the open runs shielded under `MCP_BROWSER_OPEN_TIMEOUT` and
releases its session if the call was cancelled — never leave a billed session
nobody holds. Where a session may go is `mcp/navigation.py`'s `NavigationPolicy`:
only http(s) and `about:blank`, `file:` only under
`WEB_ROUTER_MCP_ALLOW_FILE_URLS`, never metadata hosts or the session's own CDP
host:port, with IPv4 read in every spelling a browser accepts. It is enforced
before `browser_goto`; in a context `route()` installed through
`AsyncBrowserHandle.on_connect`, which sees only a chain's first request; by
recording every navigation request (redirect hops included) and every
`framenavigated` while an action runs; and after it, on the URLs of the page
and its attached frames, resolving every DNS name the recorded navigations
touched — the route resolves only a chain's first hop, so an alias hop is
caught only here. The record is what catches a redirect and a frame
removed before the action ends. Do not drop any of them. CDP
`Network.setBlockedURLs` did not block a redirect hop over `connect_over_cdp`,
so it is not used. `browser_open` sends its default `timeout` only to a backend
whose class declares it, and a clamped value is named in the result's `note`.
The `browser_*` tools are listed only where Playwright imports; the `mcp` extra
does not install it.

**A source's inputs are the source's, never ours.** `LiveDataRequest.inputs`
is sent as the caller gave it, keyed by whatever the source's `input_schema`
names; nothing here reads a URL out of it, derives one, or checks it against
the schema -- the backend refuses inputs that do not match, in its own words.
The MCP layer alone screens an input that is a URL (`mcp/urls.py`).
A run is one set of inputs: a page the backend could not fetch, or a source
that failed to parse it, raises `ProviderError` with the backend's reason.
`records` holds what came back -- one for a product page, many for a search --
and `data` the first of them. `fetched_at` is the vendor's own timestamp or
`None`: never the time the answer arrived, and never a timestamp whose meaning
the vendor does not document (Oxylabs' `created_at` stays in `extra`). A
schema the backend does not publish stays `None`, and one that lists field
names without types (Bright Data) names those fields and nothing more.
`format` is the one option, and it is the extract rule in reverse: unset
sends nothing and returns no page, since the records are the answer and there
is no vendor default to pin; a backend that returns no page declares the
option and supports no format, so asking raises `UnsupportedFormatError`. The record is read from `data.parsing` in each of
the three shapes the SDK's union allows (bare, wrapped beside `success`, or an
`error` report -- `NIMBLE_LIVE_DATA_PARSING_SUCCESS_STATUS` records which was
seen live), and only the last is a failure; a record whose own field is
called `status` is still a record. `live_data_sources()` returns one page as the
backend pages it -- `limit` and `offset` unset are not sent -- never the whole
catalogue, which runs to thousands (Bright Data answers with all of it, so its
provider pages the list itself). The MCP `live_data_sources` tool shapes a row
to the words a model picks a source by and leaves the schemas to `live_data_source`,
whose schemas are never cut: a cut `input_schema` sends the model guessing at
the inputs it dropped. A schema or a record that does not fit the budget even
on its own is left out whole, with a note naming the `fetch_raw` handle that
holds it (`MCP_MSG_OVERSIZED`), never inlined over the budget. There is no
live data `auto`: a source's name belongs to one vendor, so `provider` is
required on the three router methods and on the three MCP tools alone, and an
unknown name raises `LiveDataSourceNotFoundError`, a `ProviderError`. One call
is one set of inputs, answered through the vendor's synchronous endpoint under
that endpoint's own time cap (Apify 300 s, Oxylabs 150 s, Bright Data about a
minute, past which it raises naming the snapshot to fetch); batch inputs stay
out of scope.

**An extract call that partly fails stays partly successful.** `ExtractProvider`
fans URLs out across calls for backends that take one at a time, records each
URL that failed in `ExtractResponse.failures`, and raises only when nothing came
back at all. Do not move that handling into a provider — it is what makes a
one-URL-per-call backend behave like a batching one.

**Keep files and functions small.** Functions are limited by ruff, in
`make check`: at most 50 statements (`PLR0915`) and a cyclomatic complexity of
at most 10 (`C901`). The few functions over either today carry a
`# noqa: C901` or `# noqa: PLR0915`; such a `noqa` may be removed once its
function is split, but never added to new code. Files are limited by the shape
test, `tests/test_shape.py`: a Python file under `src/web_router/`, `scripts/`,
`benchmarks/` or `tests/` is at most 400 lines. Files already longer are
grandfathered in `tests/shape_baseline.json` and may only shrink: after
shrinking one, run `make shape-baseline`, which refuses to add or grow an
entry. Never regenerate it to admit new code; split the code instead. The
large hubs — `client.py`, `providers/auto.py`, `types.py`, `constants.py`,
`mcp/tools.py` — are in that baseline, so none of them may gain even one line
on net: new code goes in its own module, and a hub grows only if the same pull
request moves at least as many lines out of it. To add a keyword argument to
hub methods, first move something out of the hub — a helper, a group of
methods into a mixin, as `client_answer.py` holds `answer` for `client.py` —
to make the room. That counts registry entries too: a new provider's line in
`constants.py`'s `*_ENV_BY_PROVIDER` tables needs an older provider's
constants moved out to its own `constants_<provider>.py` in the same pull
request.

**Reuse before you write.** Before adding a helper, grep for an existing one
and use it.

**No hardcoded literals in logic.** Endpoints, environment variable names,
timeouts, header names and message templates belong in a constants module,
but new ones never go into `constants.py` (beyond a registry entry, paid for as
above): a provider's go in its `constants_<provider>.py`, and any others in a
module of their own next to it named for their area, `constants_<area>.py`, as
`constants_answer.py` and `constants_urls.py` already are.

**Unset means unset.** Request fields left as `None` must not be sent to the
provider, so each API keeps its own defaults instead of inheriting ours. Use
`_utils.compact()`, which `build_payload()` already does for you. There are
exactly three exceptions: an extract provider's `format` and a browser
session's `timeout`, both above, and a narrow third — `local` launches
`--headless=new` when `headless` is unset, because there is no vendor default to
keep and a window nobody asked for would open on the caller's screen.

**Preserve the full payload.** Unmapped provider fields go in
`SearchResult.extra` or `ExtractedDocument.extra`; the untouched responses go in
`SearchResponse.raw` and `ExtractResponse.raw`. Normalization must never be
lossy.

**Wrap provider failures.** Every SDK or HTTP exception becomes a
`ProviderError` carrying the original on `__cause__`.

**Never let a credential follow a redirect.** `_HttpProvider` pins
`follow_redirects=False` and refuses `follow_redirects=True` on a provider that
sends a key. `httpx` replays request headers to the redirect target without
stripping them cross-host, so following one hands that host the key. Do not
"simplify" this by dropping the pin and relying on the `httpx` default, and do
not relax the refusal. A provider with an unusual auth scheme overrides
`_auth_headers` rather than building the header itself, so it cannot opt out of
the pin by leaving `api_key` unset — Oxylabs' HTTP Basic is the example.

**Build the HTTP clients lazily.** `_HttpProvider` creates the sync and
async `httpx` clients on first use, because `close()` cannot shut an async
client and `aclose()` cannot be called from sync code — building both eagerly
leaked whichever one the caller did not use.

**Verify provider APIs against the live service, not documentation.** Vendor
docs here have been wrong, and providers disagree in ways only a real call
reveals: You.com documents an `extraction_formats` field that the API rejects
with a 422, Tavily rejects both the ISO country codes every other backend
requires and `format="html"` on extract, Jina Reader puts the page body under a
different key per format, and Bright Data's own reference shows a JSON envelope
beside a `format="raw"` request that does not return one. Before trusting a
parameter, call it. `tests/test_integration_surface.py` catches this class of
drift and runs each check only against providers that map the option — add to it
when you map one.

Where no account was available, say so in the test module's docstring the way
`tests/providers/test_oxylabs.py`, `test_brightdata.py` and `test_firecrawl.py`
do, so the next person knows the mapping is documentation-derived rather than
verified.

The one exception: a browser provider may declare `timeout` and `idle_timeout`
from documentation alone. The `BROWSER_TIMEOUT` default reaches only a provider
that declares `timeout`, so waiting for a live call would leave every session
on that backend to the vendor's own limit, which can be hours. `idle_timeout`
is the only way a caller can bound a session left open but unused, and it is
mapped from the same documentation as the lifetime beside it; refusing it
until a live call would leave that session billing too. The test module's
docstring still names each one as unverified.

## Provider contracts

`contracts/<method>/<provider>.json` records the wire fields each option value sends,
read from the provider's own code, beside three hand-written fields:

- `source`: the vendor's reference for the endpoints.
- `verified`: per mapped option, `live: <the test that calls it>` or `docs: <the page>`.
- `vendor_options`: each documented vendor option no mapping sends, `passthrough` or `not_supported`, with a note.

A contract is committed once those are filled. Start one with
`make contracts PROVIDER=<method>/<provider>`, fill them in, and commit it; after any
mapping change, `make contracts` regenerates every committed contract, keeping what
was written by hand. `tests/test_provider_contracts.py` fails when a committed
contract is stale, or its hand-written fields are partial or hold a `TODO`, and when a
built-in provider has no contract at all.

## Provider prices

Every built-in (provider, method) has a row in `src/web_router/data/prices.json`,
and `tests/test_prices.py` fails for one that does not (`auto`, `max` and
`local` are exempt). A new backend adds its rows from the vendor's own pricing
page -- `unit` `unknown` when none is published, `token_billed` when it bills by
tokens, never a guess -- then `make prices` rewrites PRICING.md's tables to
match. The `latency_*` fields are not edited by hand: `make latency` writes
them from the frozen results in `benchmarks/providers/frozen/` (search and
answer only, null elsewhere), and `tests/test_prices.py` fails while they are
stale. New frozen results mean running it again.

## Adding a live data provider

Against `LiveDataProvider`, or `HttpLiveDataProvider`
(`providers/_http_live_data.py`) for a REST API. First check the rule: the
vendor's sources fetch the site on request. A catalogue of data collected
earlier belongs in the vendor catalogue for `call_vendor`, not here.

1. Implement `_run`/`_arun` -- one request in, one `LiveDataResponse` out,
   every record in `records` -- `_list`/`_alist`, returning one
   `LiveDataSourcePage` with `limit` and `offset` sent only when set (or
   applied locally, if the vendor answers with the whole catalogue), and
   `_get`/`_aget`, one `LiveDataSource` by name with `input_schema` and
   `output_schema` from the backend -- `None` when it publishes none. Set
   `name`, `supported_parameters` (`LIVE_DATA_PARAMETERS`, so an unservable
   `format` is refused as a format) and `supported_formats`, declare the run
   endpoint as `run_path`, and give the body a `_build_payload` so the
   contract can record it. A fetch the backend refused, a page the site
   answered with 4xx or 5xx, or a parse that failed, raises `ProviderError`
   naming the source and the backend's reason; an unknown name raises
   `LiveDataSourceNotFoundError` (`HttpLiveDataProvider._named` maps a 404).
   Set `fetched_at` only from a timestamp the vendor documents as the fetch time.
2. Put the paths, field names and status values in a constants module of
   their own (`constants_apify.py`, `constants_live_data_vendors.py`).
3. Add the class to `BUILTIN_LIVE_DATA_PROVIDERS` and its variables to
   `LIVE_DATA_ENV_BY_PROVIDER`, both in `constants_live_data.py`. There is no
   `auto` to join.
4. Add `tests/providers/test_<name>_live_data.py` covering the payload, each
   response shape the docs describe (records, a refused fetch, a failed
   parse), the catalogue mapping, and a skipping `@pytest.mark.integration`
   live test that lists, describes and runs one source.
5. A price row in `prices.json` (`make prices`), a contract (`make contracts
   PROVIDER=live_data/<name>`), the README support table and `CHANGELOG.md`.

## Adding a research provider

The same shape as the two below, against `ResearchProvider` or
`HttpResearchProvider`.

1. Implement `_start`/`_astart` — start the run, return a `ResearchProgress`
   carrying the backend's own job id — and `_fetch`/`_afetch`, which report
   where a started run is and attach the answer once `state` is `completed`.
   A backend whose status GET carries the answer subclasses
   `HttpPolledResearchProvider` instead and declares its paths and status
   table rather than writing these. Set `name` and `supported_parameters`; declare `output_schema` only if the
   backend really returns structured output, because a backend that does not
   should raise `UnsupportedParameterError` rather than hand back prose under
   the name of a schema.
2. Put the endpoints, the effort mapping and the run-status table in
   `constants_<provider>.py`; read the status table through `state_of`.
3. Register the class in `_BUILTIN_RESEARCH_PROVIDERS` in `registry.py` and add
   the provider to `RESEARCH_ENV_BY_PROVIDER` in `constants.py`.
4. Add `tests/providers/test_<name>_research.py` covering the mapping, the
   submit-poll-result sequence and an unrecognized status, plus a skipping
   `@pytest.mark.integration` live test.
5. Update the README support table and `CHANGELOG.md`.

## Adding an answer provider

Search's shape, against `HttpAnswerProvider` — one POST, one answer — or
`AnswerProvider` for an SDK.

1. Implement `_build_payload` and `_to_response` and declare `answer_path`
   (or `_answer`/`_aanswer` on `AnswerProvider`). Set `name` and
   `supported_parameters`, declaring an option only after a live call showed
   the backend honouring it. The answer is the backend's own: never edit,
   join or summarize it. An empty one is returned as `""`, not raised. Put an
   inline citation marker (`[2]`, `[[1, 2]]`) in `Citation.field`, set
   `outcome` to `partial` only when the backend says it stopped early, and
   keep every payload in `raw`. A combination the vendor forbids — both
   domain lists where it takes one — raises `UnsupportedParameterError`
   before the call.
2. Put the endpoint and field names in `constants_<provider>.py`.
3. Register the class in `_BUILTIN_ANSWER_PROVIDERS` in `registry_answer.py` and add
   the provider to `ANSWER_ENV_BY_PROVIDER` in `constants.py`. There is no
   `auto` to join yet.
4. Add `tests/providers/test_<name>_answer.py` covering the payload, each
   mapped option and the response against a recorded payload, plus a skipping
   `@pytest.mark.integration` live test. `tests/test_answer_contract.py`
   already holds every unmapped option to a refusal before anything is sent.
5. Start its contract with `make contracts PROVIDER=answer/<name>`, and update
   the README support table and `CHANGELOG.md`.

## Adding a browser provider

The same shape again, against `BrowserProvider` or `HttpBrowserProvider`.

1. For an API with no SDK, subclass `HttpBrowserProvider` — copy `steel.py`: implement
   `_build_payload` and `_to_session`, and declare `open_path`,
   `release_path` (with `{session_id}` in it) and `release_method`, plus
   `release_body` if the release is an update and `_check_release` if a 200
   can still report a refusal. For an SDK,
   subclass `BrowserProvider` and implement `_open`/`_aopen` and
   `_release`/`_arelease`. Implement `_fetch`/`_afetch` only if the API can
   say where a session is; release uses it to tell an expired session from a
   failed release. Set `name` and `supported_parameters`, starting at
   `{"timeout"}` if the API has a lifetime limit. If it maps `stealth`, set
   `stealth_levels = frozenset(ACME_STEALTH)` from its level table, so the two
   cannot drift; a contract test holds them equal. Set `proxy_countries` when
   the vendor publishes its country list, and leave it `None` when it does
   not, rather than building one. Declare `co_requirements` when the vendor
   quietly ignores an option without another, as Anchor ignores stealth
   without its proxy.
2. Put `cdp_url` in the `SecretStr` field and nowhere a `repr` reaches, and an
   interactive live view in `control_url` the same way. Put the
   endpoints and the session-status table in `constants_<provider>.py`; read
   the status table through `state_of`.
3. Register the class in `_BUILTIN_BROWSER_PROVIDERS` in `registry.py` and add
   the provider to `BROWSER_ENV_BY_PROVIDER` in `constants.py`. Add each
   variable to `.env.example`. A REST vendor is plain httpx and registers
   against `all`; the `browser` extra is Playwright, for the page actions, and
   opening a session never needs it.
4. Add `tests/providers/test_<name>_browser.py` covering the mapping, the
   open-release sequence and an unrecognized status, plus a skipping
   `@pytest.mark.integration` live test that opens, asks and releases, and
   asks the backend afterwards through `tests/providers/_browser_live.py`.
5. Update the README support table and `CHANGELOG.md`.

## Adding a browse provider

Research's shape, against `HttpBrowseProvider` — every built-in task vendor is
plain httpx — or `BrowseProvider` for an SDK.

1. Build each request with the helpers `HttpBrowseProvider` gives you —
   `_post`, `_get`, `_call` for a verb or a bodiless answer, and `_task_path`
   for an id in a path — so the redirect pin, the auth header and the
   caller's remaining wait apply to every one. Implement `_start`/`_astart` — start the task, return a `BrowseProgress`
   carrying the backend's own task id — and `_fetch`/`_afetch`, which report
   where it is and attach the `BrowseResponse` once `state` is `completed`;
   `_cancel`/`_acancel` if the API can stop one. A task call that blocks until
   the agent is done sets `resumable = False` and implements `_start` alone.
   Set `name` and `supported_parameters`; declare `output_schema` only if the
   backend really returns structured output. A backend that cannot start
   without `start_url` lists it in `required_parameters` as well, and the base
   class raises `MissingParameterError` before anything is billed — never fold
   the URL into the task text; `missing_parameters()` is that check, on the
   class. The agent's model goes through `provider_options`, not a normalized
   field. Declare `max_steps` and `allowed_domains` only after a live call has
   shown the backend honours them — unlike a browser's `timeout`, there is no
   documentation-only exception for these.
2. Set `structured` only when the backend says the output conforms to a schema
   the caller requested — through `output_schema`, or a vendor-native schema in
   `provider_options` such as Browserbase's `resultSchema`. A dict whose keys
   the agent chose stays raw-only. Map `steps` only from what the backend
   reports, never from the final answer, and keep the untouched payloads in
   `raw`, which stays out of `repr` and
   serialization because a task payload can carry a session URL. Keep
   live-view, session and recording-control URLs out of plain fields; they
   stay in `raw`.
3. Put the endpoints and the task-status table in `constants_<provider>.py`;
   read the table through `state_of`. Register the class in `_BUILTIN_BROWSE_PROVIDERS`
   in `registry.py`, add the provider to `BROWSE_ENV_BY_PROVIDER`, and each
   variable to `.env.example`.
4. Add `tests/providers/test_<name>_browse.py` covering the mapping, the
   submit-poll-result sequence and an unrecognized status, plus a skipping
   `@pytest.mark.integration` live test.
5. Update the README support table and `CHANGELOG.md`.

## Adding a search provider

1. `src/web_router/providers/<name>.py`. For an API with no SDK, subclass
   `HttpSearchProvider` and implement only `_build_payload` and `_to_response`
   — copy `perplexity.py`, it is the shortest. For an API with an SDK worth using,
   subclass `SearchProvider` and implement `_search`/`_asearch`. Either way set
   `name` and `supported_parameters` (start at `{"max_results"}`), and put the
   endpoint and parameter mappings in `constants_<provider>.py`. A keyless
   endpoint sets `env_api_key = None` and sends no auth header.
2. Map the payload onto `SearchResponse`. Use `_utils.best_text()` to pick the
   result body — providers disagree wildly about which field carries it.
3. Add the SDK to the extra for each method the provider serves in
   `pyproject.toml` (`search`, `extract`, `research` — extras are per method,
   never per vendor; `mcp` is the one exception and names a transport),
   register the module in
   `_BUILTIN_PROVIDERS` in `registry.py`, and add the provider to
   `ENV_BY_PROVIDER` in `constants.py` (`None` if it needs no key) —
   `configured_providers()` reads that table and a contract test enforces it.
4. Add `tests/providers/test_<name>.py` covering parameter mapping and response
   normalization against a recorded payload, plus one `@pytest.mark.integration`
   live test that skips without credentials.
5. Update the README support table and `CHANGELOG.md`.

## Adding an extract provider

The same shape, against the extract side of each file. A vendor that already
searches gets a second class in its existing module rather than a new one.

1. Subclass `HttpExtractProvider` (implement `_build_payload` and
   `_to_response` — copy `firecrawl.py`, it is the shortest) or
   `ExtractProvider` for an SDK-backed API. Set `name`,
   `supported_parameters`, `supported_formats` — derive it from your format
   table, `frozenset(ACME_EXTRACT_FORMATS)`, so the table stays the single
   description — and `default_format`. Set `batches_urls = True` only if one
   call really takes every URL; leave it alone and the base class fans out.
2. Map the payload onto `ExtractResponse`. If the API answers 200 and reports a
   refused fetch in a status field, return that URL as an `ExtractFailure`
   rather than a document with empty content — Nimble and Firecrawl both do
   this, and an empty document would hide it.
3. Register the module in `_BUILTIN_EXTRACT_PROVIDERS` in `registry.py`, and add
   the provider to `EXTRACT_ENV_BY_PROVIDER` in `constants.py` — a tuple of
   every variable it needs, `()` for a keyless one. Add each variable to
   `.env.example`.
4. Add `tests/providers/test_<name>.py` and the live checks in
   `tests/test_integration_surface.py`, which run only against providers that
   declare the format under test.
5. Update the README support tables and `CHANGELOG.md`.

## Testing

Unit tests must not touch the network — register a fake provider (see
`tests/conftest.py`). Live tests are marked `@pytest.mark.integration`, skip
themselves when the relevant key is absent, and are excluded from `make test`.

`make test` runs with no secrets at all, so anything needing a key must carry
that marker or it will fail there.

Tests that launch a real local browser are marked `@pytest.mark.browser`, skip
when no Chromium is installed, and are excluded from `make test`; run
them with `make test-browser`.

Assert on behavior a user depends on, not on internal shape. Prefer one test
that proves a mapping is right over three that restate the implementation.

Tests of `evaluate()` and the judge use a fake `Judge` subclass, or a fake
`openai` or `anthropic` module for `OpenAIJudge`'s or `AnthropicJudge`'s own
plumbing — never the network, and never `OPENAI_API_KEY` or
`ANTHROPIC_API_KEY`.

## Style

[STYLE_GUIDE.md](STYLE_GUIDE.md) is the full version and takes precedence. In short:

- Python 3.10+, `from __future__ import annotations`, full type annotations.
  `mypy --strict` must pass on `src/`.
- Line length 120, ruff-formatted.
- Comments explain *why*, never *what*. Do not narrate the code, leave TODOs
  about work you were asked to do, or add banner headers inside a class.
- Docstrings on public API; skip them on self-evident private helpers.
- Prefer a lookup table in a constants module over a chain of `if`.

## Pull requests

Open PRs as drafts: `gh pr create --draft --body-file <file>`, with the body following
`.github/pull_request_template.md` (same headings, comments removed). Draft PRs do not request
the code owners' review yet. Mark ready only after Qodo's "Code Review by Qodo" comment covers the
latest commit and every finding has a reply in its thread, starting with `@qodo`:
`Fixed in <sha>: <what>` or `Not fixing: <reason>`. Qodo does not review drafts on its own, so a
`/agentic_review` comment on the draft is a good way to ask for one.

A change users would notice adds a line under `## [Unreleased]` in `CHANGELOG.md`; anything else
(CI, docs, tests, refactors) takes the `skip-changelog` label. The Changelog check fails the PR otherwise.

## Files for feedback

Don't commit drafts, decks, PDFs or images for feedback; open a
[Discussion](https://github.com/WebContextLayer/webrouter/discussions) and attach them.

## Secrets

Never commit API keys, write them into tests, or paste them into code. Keys come
from the environment (`NIMBLE_API_KEY`, `TAVILY_API_KEY`, `YOU_API_KEY`,
`BRAVE_SEARCH_API_KEY`, `EXA_API_KEY`, `PARALLEL_API_KEY`,
`PERPLEXITY_API_KEY`, `LINKUP_API_KEY`, `WEBIQ_API_KEY`, `KEENABLE_API_KEY`, `DEEPSEEK_API_KEY`, `FIRECRAWL_API_KEY`, `OXYLABS_USERNAME` +
`OXYLABS_PASSWORD`, `BRIGHTDATA_API_TOKEN` + `BRIGHTDATA_ZONE`,
`STEEL_API_KEY`, `BROWSERBASE_API_KEY`, `HYPERBROWSER_API_KEY`,
`ANCHOR_API_KEY`, `KERNEL_API_KEY`, `BROWSER_USE_API_KEY`, `SKYVERN_API_KEY`, and the optional
`JINA_API_KEY`) or from `provider_config` at construction. `agentcore` reads
`AGENTCORE_GATEWAY_URL`, which is not a secret; its credential is the AWS one
botocore resolves, never a key of ours. `.env.example` lists
every one, beside `CHROME_PATH`, which is a path for `local` rather than a
secret; `WEB_ROUTER_ENV_FILE`, the path of the `.env` the MCP server loads, is
not a secret either and comes from the real environment only, since no file can
set it. `ENV_BY_PROVIDER`, `EXTRACT_ENV_BY_PROVIDER`,
`RESEARCH_ENV_BY_PROVIDER`, `ANSWER_ENV_BY_PROVIDER`, `BROWSER_ENV_BY_PROVIDER` and
`BROWSE_ENV_BY_PROVIDER` in `constants.py` are the machine-readable versions.
`auto`, `max`, `you-mcp`, `exa-mcp`, `parallel-mcp`, `jina` and `local` need none, and
Tavily, Firecrawl and Keenable run keyless with `allow_keyless`, as `auto` uses them. `OPENAI_API_KEY` and
`ANTHROPIC_API_KEY` are the two library keys that are not a provider's: each is
read only by its judge behind `evaluate()` (the `evals` extra) —
`OpenAIJudge` and `AnthropicJudge` — so each appears in `.env.example` and in
no `*_ENV_BY_PROVIDER` table, and no search, extract, research or browser call
ever touches either. `benchmarks/agent/` reads both too, for its agents —
`ANTHROPIC_API_KEY` for Claude's, `OPENAI_API_KEY` for OpenAI's; the Claude and
OpenAI tools never read either key, because they are
definitions the caller's own client sends. `.env` is gitignored — keep it that way. `LIVE_DATA_ENV_BY_PROVIDER`, in
`constants_live_data.py`, is live data's table, and names Nimble's, Bright
Data's and Oxylabs' keys again: one key, several classes.

**The library never loads `.env` itself.** Only these entry points do:
`tests/conftest.py`, for the live tests, `benchmarks/agent/run.py`,
`benchmarks/providers/run.py`, `benchmarks/providers/extract.py` and
`benchmarks/providers/answer.py`, scripts
run from the repo, and the MCP server —
its `main()` at startup and its `load_env` tool at runtime, both through
`mcp/env.py` — because an application started by a client, often installed
from PyPI with no repo beside it and started in whatever directory the client
picks, has no other way to see keys nobody exports. At startup it reads
every one of four places, in this order: the path in
`WEB_ROUTER_ENV_FILE` (`ENV_MCP_ENV_FILE`); `MCP_ENV_FILE` in
`user_config_dir()` — XDG on Linux, Application Support on
macOS, `%APPDATA%` on Windows, from `MCP_USER_CONFIG_ROOT_BY_PLATFORM` rather
than a `platformdirs` dependency the `mcp` extra does not otherwise have; the path
`load_env(remember=true)` recorded in `MCP_ENV_POINTER_FILE`; and
`MCP_ENV_FILE` in its working directory — the repo root when `.mcp.json`
launches it. An earlier place wins per variable and a later file fills only
what is still unset -- a two-variable credential only as a whole -- so a project's own `.env` and a remembered path can add
keys but never replace the user's; the capabilities resource's `env` object
names the file each variable came from, and reports a `WEB_ROUTER_ENV_FILE`
naming no file, never skipping it silently. Every file, from
wherever, is read on the same terms: only `credential_variables()` — every
variable `ALL_ENV_TABLES` in `mcp/env_credentials.py` names, and Jina's optional key — a user-scoped server can be started from any
workspace, and a foreign `.env` must not flip `WEB_ROUTER_MCP_ALLOW_FILE_URLS`
or aim `CHROME_PATH` at its own binary; a variable already set wins; nothing is
interpolated, so `${OTHER}` cannot copy another secret into a provider's key;
and finding no file is a no-op, so a fresh install starts on the keyless tier.
`load_env` takes a path from the model, never a value, and refuses anything
that is not a small text file; since it reads only credential variables,
pointing it at any other file yields nothing, and neither its result, its
errors nor the capabilities resource ever carries a value — names and paths
only. `remember` writes the *path* to `MCP_ENV_POINTER_FILE`, never a copy of
the keys. A load that set anything makes `WebRouterTools` swap in a router from
its `router_factory`, because a router resolves `auto`'s chain once and caches
every provider it built; the old router is kept until shutdown, since open
browser sessions and polled jobs still run on its providers. The server then
sends `notifications/tools/list_changed`, advertised as `tools.listChanged`;
on the 2026-07-28 wire the SDK drops it, since this server serves no
`subscriptions/listen`.
`.mcp.json` launches it as `uv run --all-extras web-router-mcp` and nothing
more: `--all-extras` is what makes a fresh clone work, with every tool the
browser's included, because a bare `uv run` syncs no extras and the server's
SDK lives in one; no `--env-file` (uv refuses
a missing file) and no shell wrapper (`sh -c` broke on Linux clients).
Everything else reads plain `os.environ`. Do not add a `load_dotenv` call
anywhere in the library proper —
a library that reads dotfiles behind its caller's back surprises whoever
embeds it.

If `web-router://capabilities` reports only the keyless backends (`auto`,
`max`, `you-mcp`, `exa-mcp`, `parallel-mcp`, `jina`, `local`) on a machine that should have keys, the keys are
not reaching the server, not missing from the account: read its `env` object —
every place the server looked, what it found there, the file it used and the
variables it loaded, by name — and check that the file holds them and that the
server was restarted since (an MCP server keeps the environment it started
with), or ask the user for the file's path and call `load_env` with it. If a
key is genuinely absent, ask the user which providers they hold credentials
for and have them put the value in a `.env` themselves — the one in the user
config directory is read wherever the server starts — never ask them to paste
a key into the conversation, and never write one into a file for them.
