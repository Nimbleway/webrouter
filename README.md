<p align="center">
  <img src="https://raw.githubusercontent.com/Nimbleway/webrouter/main/docs/assets/webrouter-mascot.png" alt="WebRouter" width="360">
</p>

<h1 align="center">WebRouter</h1>

<p align="center"><strong>The open source standard for agent web access.</strong><br>
One typed interface for search, extract, answer, research, live data and browsers, across two dozen providers.</p>

<p align="center">
  <a href="https://pypi.org/project/webrouter/"><img src="https://img.shields.io/pypi/v/webrouter?color=202c26&label=pypi" alt="PyPI"></a>
  <a href="https://github.com/Nimbleway/webrouter/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-202c26" alt="License: Apache 2.0"></a>
  <a href="https://pypi.org/project/webrouter/"><img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-202c26" alt="Python 3.11 to 3.13"></a>
  <a href="https://github.com/Nimbleway/webrouter/actions/workflows/ci.yml"><img src="https://github.com/Nimbleway/webrouter/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="https://discord.gg/MGztFWSjZh"><img src="https://img.shields.io/badge/Discord-5865F2?logo=discord&logoColor=white" alt="Join WebRouter on Discord"></a>
</p>

<p align="center">
  <a href="https://docs.webrouter.net"><strong>Documentation</strong></a> ·
  <a href="https://docs.webrouter.net/quickstart">Quickstart</a> ·
  <a href="https://docs.webrouter.net/providers">Providers</a> ·
  <a href="https://github.com/Nimbleway/webrouter/tree/main/examples">Examples</a> ·
  <a href="https://discord.gg/MGztFWSjZh">Discord</a>
</p>

> **Developer preview:** the API may change before 1.0. Share feedback on [Discord](https://discord.gg/MGztFWSjZh).

Every web API for agents has its own SDK, parameter names and response shape.
WebRouter puts one interface in front of all of them, so switching provider is
one argument, a failing provider falls back to the next, and your agent code
never changes.

```python
from web_router import WebRouter

with WebRouter() as router:  # no API key needed to start
    results = router.search("what is retrieval augmented generation", max_results=5, timeout=10)
    pages = router.extract(results.urls[:3], timeout=30)  # markdown, whichever backend serves it

print(results.provider, [doc.url for doc in pages.documents])
```

## Why WebRouter

- **One call shape, every provider.** The same request and the same typed
  response from Tavily, Exa, Brave, Perplexity, Firecrawl, Nimble and the rest.
  Nothing is lost: unmapped fields stay in `extra` and the raw payload in `raw`.
- **Never silently weaker.** An option a provider cannot honour raises
  `UnsupportedParameterError` instead of being dropped, so a call never runs
  under looser constraints than you asked for.
- **Routing based on published benchmarks.** `auto` tries the providers you hold keys for in the
  order committed benchmark results rank them, and falls back on an error or an
  empty answer. `response.provider` always says who answered.
- **Cost control.** Cap a call or a whole session in dollars and seconds, with
  list prices for every provider shipped in the package
  ([experimental](#budgets-and-cost-control)).
- **Keyless to start.** Search, extract and a local browser work with no API
  key. Every key you add unlocks more providers and fallbacks.
- **Built for agents.** An MCP server, plus ready-made tools for LangChain,
  Claude and OpenAI agents.
- **Measure, don't guess.** `benchmark()` and `evaluate()` compare providers on
  your own queries and score their accuracy against known answers.
- **Yours to run.** Apache 2.0, runs in your process with your own keys, and
  sends each key only to its own provider.

## Install

```bash
pip install "webrouter[all]"     # or: uv add "webrouter[all]"
```

Python 3.11 or later. Extras per method (`search`, `extract`, `research`,
`answer`, `browser`, `mcp`, ...) keep the install small; see the
[Quickstart](https://docs.webrouter.net/quickstart).

Keys are read from the environment. Copy [`.env.example`](.env.example), which
names every variable and where to get it, and fill in the ones you hold.

## Methods

| Method | What it does | Keyless |
| --- | --- | --- |
| [`search`](https://docs.webrouter.net/search) | Web results for a query | Yes |
| [`extract`](https://docs.webrouter.net/extract) | Page contents for URLs, in markdown, html or text | Yes |
| [`answer`](https://docs.webrouter.net/answer) | A written answer with its sources, in one call | No |
| [`research`](https://docs.webrouter.net/research) | A multi-step research run, as prose or a schema you give | No |
| [`live_data`](https://docs.webrouter.net/live-data) | A vendor's ready-made scraper for one site, returning records | No |
| [`open_browser`](https://docs.webrouter.net/browser) | A browser session you drive, local or in the cloud | Yes (`local`) |
| [`browse`](https://docs.webrouter.net/browser) | A task handed to a hosted browser agent | No |
| [`call_vendor`](https://docs.webrouter.net/vendors) | A vendor's own API (Google Ads, Datadog, ...) with your credentials | No |

```python
router.answer("who won the 2022 world cup final")  # .answer, .citations
router.research("how did nvidia's datacenter mix shift in 2025", output_schema=MySchema)
router.live_data("amazon_products", {"url": product_url}, provider="brightdata")

with router.open_browser() as browser:  # a local Chromium, no key
    browser.goto("https://example.com")
```

Every method has an async twin on `AsyncWebRouter`.

## Providers

| Method | Providers |
| --- | --- |
| Search | Nimble, Tavily, Exa, Brave, Perplexity, Parallel, Linkup, You.com, WebIQ, Keenable, DeepSeek, AWS AgentCore, plus the keyless You.com, Exa and Parallel MCP endpoints |
| Extract | Nimble, Tavily, Firecrawl, Exa, Linkup, Oxylabs, Bright Data, Jina Reader, WebIQ, Keenable, DeepSeek, Browserbase |
| Answer | Perplexity, You.com, Linkup, Parallel, Exa, Brave |
| Research | Nimble, Parallel, Perplexity, Linkup, You.com |
| Live data | Nimble, Apify, Bright Data, Oxylabs |
| Browser sessions | Local Chromium, Browserbase, Steel, Hyperbrowser, Anchor, Kernel |
| Browser agents | Browserbase, Browser Use, Skyvern |

[Providers](https://docs.webrouter.net/providers) has each provider's key, the
options it maps and how it was verified. Adding one is two methods: see
[Adding a provider](https://docs.webrouter.net/custom-providers).

## Routing and fallback

For search, extract, answer, research and the browser methods, name no
provider and `auto` serves the call: every provider you hold a key for, in the
order committed [benchmark results](benchmarks/providers/README.md) rank them,
then the free tiers. A member that fails, is rate-limited or answers empty
passes the call to the next. A member that cannot honour an option you set is
skipped for that call, and if none can, the call raises rather than running
without it. Live data and vendor calls always name their provider, since a
source or an account belongs to one vendor.

```python
from web_router import Provider, WebRouter

WebRouter(Provider.TAVILY).search("...")  # one provider, by name
WebRouter().search("...", provider="exa")  # or per call

router = WebRouter(provider_config={"auto": {"chain": {"extract": ["nimble", "firecrawl"]}}})
router.extract(url)  # Nimble, then Firecrawl if Nimble fails
```

A named provider is never quietly swapped for another: a missing key raises an
error naming the variable to set. In a service, share one router per process,
set `timeout=` on each call and turn on the `web_router` logger: see
[Running in production](https://docs.webrouter.net/production). [The auto order](https://docs.webrouter.net/providers#the-auto-order)
lists each method's order and how it is generated.

## Budgets and cost control

*Experimental: names may change before 1.0.*

```python
from web_router import Budget, WebRouter, prices

router = WebRouter()
router.search("...", budget=Budget(max_cost_usd=0.01, time_limit_s=10))

with router.budget(Budget(max_cost_usd=3.0, time_limit_s=600)) as b:
    b.search("...")
    b.research("...")
    b.status()  # spent, reserved, remaining, time left

prices.estimate("tavily", "search")  # 0.008: list price of one request
```

Each attempt is checked against what is left before it starts, so parallel
calls never overspend together, and a backend that does not fit is skipped
with the reason recorded. Costs are estimates from the list prices shipped in
`web_router.prices`, not your bill. An optional router can also pick the
method and provider for each prompt within the budget, and
`url_policy="public_only"` keeps a planned call off private and cloud-metadata
hosts. See [Smart routing](https://docs.webrouter.net/smart-routing).

## Use it from an agent

**MCP server.** Every method as a tool, running on your machine with your keys:

```bash
claude mcp add web-router -- uvx --from "webrouter[mcp]" web-router-mcp
```

The [MCP guide](docs/mcp.md) covers Claude Desktop, Cursor and other clients,
credentials, tool costs and browser guards.

**Agent frameworks.** Ready-made `web_search` and `web_extract` tools, with a
compact rendering for the model and the full response kept for you:

```python
from web_router import WebRouter
from web_router.integrations.langchain import web_router_tools

tools = web_router_tools(WebRouter(), max_results=5)  # LangChain
```

`web_router.integrations.anthropic` and `web_router.integrations.openai` give
Claude and OpenAI agents the same tools as plain tool definitions. See
[Agent integrations](https://docs.webrouter.net/integrations).

## Compare providers on your own queries

```python
report = router.benchmark("nvidia q3 earnings", providers=["nimble", "tavily", "you-mcp"])
for run in report.runs:
    print(run.provider, run.latency, run.count if run.ok else run.error)
```

`benchmark_extract()` does the same for extract, and `evaluate()` scores each
provider's accuracy on a question set with known answers (SimpleQA, FRAMES,
SealQA or your own CSV) with an LLM judge. See
[Benchmarks and evaluation](https://docs.webrouter.net/benchmarks), and
[`benchmarks/`](benchmarks/) for the published runs behind the default order.

## WebRouter or calling each provider directly

| | Each provider's SDK | WebRouter |
| --- | --- | --- |
| Switching provider | Rewrite the call, options and parsing | One argument |
| Response shape | Different per provider | One typed model, raw payload kept |
| An unsupported option | Varies by provider | Raises, naming who can serve it |
| A provider fails | Your retry code | Falls back to the next you hold a key for |
| Which provider to use | Guesswork | Order from published benchmarks, `benchmark()` on your queries |
| Spend | Per-vendor dashboards | Per-call and per-session caps, list prices in the package |
| Agent tools | One integration per provider | One MCP server and one set of tools |

## Documentation

| Get started | Methods | Go further |
| --- | --- | --- |
| [Introduction](https://docs.webrouter.net) | [Search](https://docs.webrouter.net/search) | [Agent integrations](https://docs.webrouter.net/integrations) |
| [Quickstart](https://docs.webrouter.net/quickstart) | [Extract](https://docs.webrouter.net/extract) | [Smart routing](https://docs.webrouter.net/smart-routing) |
| [How it works](https://docs.webrouter.net/how-it-works) | [Answer](https://docs.webrouter.net/answer) | [Benchmarks](https://docs.webrouter.net/benchmarks) |
| [Providers](https://docs.webrouter.net/providers) | [Research](https://docs.webrouter.net/research) | [Adding a provider](https://docs.webrouter.net/custom-providers) |
| [Configuration](https://docs.webrouter.net/configuration) | [Live data](https://docs.webrouter.net/live-data) | [MCP server](docs/mcp.md) |
| [Errors](https://docs.webrouter.net/errors) | [Browser](https://docs.webrouter.net/browser) | [Examples](examples/) |
| | [Vendor data](https://docs.webrouter.net/vendors) | [Changelog](CHANGELOG.md) |

## Contributing

Contributions are welcome, and the most useful one is small: map one more of a
provider's options, or add a provider.

```bash
make install  # every extra, dev tools and pre-commit hooks
make check    # lint, typecheck and the offline unit tests; no keys needed
```

[CONTRIBUTING.md](CONTRIBUTING.md) has the workflow and the provider checklist,
[STYLE_GUIDE.md](STYLE_GUIDE.md) the review standards, and
[AGENTS.md](AGENTS.md) the rules for AI coding agents working on this repo.

## Community

- [Discord](https://discord.gg/MGztFWSjZh): questions in #help, and what you built in #showcase.
- [GitHub Issues](https://github.com/Nimbleway/webrouter/issues): bugs and gaps in a provider's support.
- [GitHub Discussions](https://github.com/Nimbleway/webrouter/discussions): proposals and design ideas.
- [SECURITY.md](SECURITY.md): vulnerabilities. Never in a public channel.

<p align="center"><em>Useful? Star the repo so more agent developers can find it.</em> ⭐</p>

## License

[Apache License 2.0](LICENSE). The SimpleQA grader prompt vendored at
`src/web_router/evals/prompts/` keeps its own MIT license, in
`LICENSE-simple-evals` beside it.
