# web-router

**One interface over many web search, extraction, answer, research and browser APIs.**

> **Alpha.** You have early access to `web-router` under your agreement with
> Nimble. Please don't share the code or this repository. Issues and pull
> requests are welcome here.

[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](pyproject.toml)
[![Typed](https://img.shields.io/badge/typing-strict-blue.svg)](https://mypy-lang.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Twelve search backends, seven extract backends, a template catalogue, five
answer backends, three agentic research backends (plus Exa's, retired upstream), five
browser-session backends and three browser agents. One call each. Switching
between them is one argument.
Maintained by [Nimble](https://nimbleway.com), one of the backends it routes to.

```python
from web_router import Provider, WebRouter

WebRouter().search("what is retrieval augmented generation")  # no key needed
WebRouter(Provider.NIMBLE).search("...")  # or name one
WebRouter(Provider.TAVILY).search("...")  # same call, same result shape

WebRouter().extract("https://example.com")  # page contents, also keyless

WebRouter().run_template("walmart_pdp", {"product_id": "..."}, provider="nimble")  # one page, one record

WebRouter(Provider.LINKUP).answer("...")  # a written answer with its sources, in one call

WebRouter(Provider.NIMBLE).research("...")  # an agentic run: minutes, not milliseconds

with WebRouter("local").open_browser() as browser:  # a browser to drive, also keyless
    browser.goto("https://example.com")

WebRouter(Provider.BROWSER_USE).browse("...")  # a hosted browser agent does the task
```

Every provider has its own SDK, its own parameter names and its own response
shape. "How many results" is `max_results` on Tavily, `count` on Brave and
You.com, `numResults` on Exa. The page text is `content` on Tavily, `text` on
Exa, `snippet` on Perplexity, and nested under `contents` on You.com. Swapping
backends means rewriting the call, the options and the parsing.

web-router normalizes all three:

```python
response = router.search(query, max_results=5, search_depth="fast")

[result.content for result in response.results]  # same field, every backend
```

The extract backends disagree just as widely. A page body is `data.markdown` on
Nimble, `raw_content` on Tavily, `text` on Exa, `results[].content` on Oxylabs
and `body` inside an envelope on Bright Data — and most of them take one URL per
call while two take a list:

```python
pages = router.extract(response.urls[:5], format="markdown")

[document.content for document in pages.documents]  # same field, every backend
```

Nothing is hidden to get there. Unmapped provider fields stay in
`SearchResult.extra` / `ExtractedDocument.extra`, the untouched payloads stay in
`raw`, and an option a backend cannot honour **raises instead of being quietly
dropped** — so a call never runs under weaker constraints than you asked for.

> **Status: early preview.** `search`, `extract`, `run_template` (with
> `templates` and `template` beside it), `research`, `open_browser`
> and `browse` are implemented; `benchmark()` and `benchmark_extract()`
> compare search and extract backends on your own queries and URLs, and
> `evaluate()` scores search accuracy with an LLM judge —
> see [Benchmarking providers](#benchmarking-providers) and
> [Evaluating accuracy](#evaluating-accuracy). The API may change before 1.0.

## Install

`web-router` is installed from this repository, not from PyPI. The one
prerequisite is [uv](https://docs.astral.sh/uv/getting-started/installation/),
which fetches the right Python itself.

```bash
git clone https://github.com/WebContextLayer/webrouter.git
cd webrouter
make setup              # every extra: the library, the MCP server, the browser tools and a local Chromium
cp .env.example .env    # then fill in only the keys you hold; none is required
```

Using a coding agent? Tell it *"clone https://github.com/WebContextLayer/webrouter
and set it up"*. [CLAUDE.md](CLAUDE.md) walks it through the rest.

**From your own project**, install the clone in editable mode, so a `git pull`
in the clone is the whole update:

```bash
uv add --editable "/path/to/webrouter[all]"     # a uv project
pip install -e "/path/to/webrouter[all]"        # any other virtualenv
```

To pick up a new release: `git pull && make setup` in the clone.

You can install fewer extras by naming them in place of `all`:

`search`, `extract`, `research`, `browser`, `mcp`, `evals` and `langchain`.
Extras are named after the **methods you call**, not the backends behind them:
`search` brings in every provider that can serve one, so choosing between them
stays a runtime decision — `WebRouter(Provider.BRAVE)` — instead of something you have
to get right at install time.

Three extras are not methods. `mcp` names a transport — it installs the
[MCP server](#the-mcp-server), which exposes the methods above rather than
adding one of its own. It does not include `browser`: the server's `browser_*`
tools need Playwright, so they appear only with `web-router[mcp,browser]`.
`evals` installs the OpenAI and Anthropic SDKs behind the built-in judges that
[`evaluate()`](#evaluating-accuracy) grades answers with; bring your own judge
and you do not need it. `langchain` installs `langchain-core` for the
[LangChain tools](#langchain) — the agent loop and the model stay your own
install. `browser` is named after what it lets a browser
handle do — drive the page — rather than after `open_browser`, which needs no
Playwright: a vendor session opens over httpx, which `[all]` installs, and
`local` needs only the standard library and a Chrome or Chromium.

## Quickstart

No key needed to try it — the default provider uses the backends that offer an
anonymous tier:

```python
from web_router import WebRouter

response = WebRouter().search("what is retrieval augmented generation", max_results=5)

for result in response.results:
    print(result.title, result.url)

print(response.provider)  # which backend actually answered
```

Name a provider once you have a key for one. `Provider` is an enum of the
built-in backends, and it is the form worth writing: your editor completes it,
and a typo is caught where you type it rather than at the first call.

```python
from web_router import Provider, WebRouter

router = WebRouter(Provider.NIMBLE)  # reads NIMBLE_API_KEY from the environment
response = router.search("who founded nimbleway", max_results=5, country="US")

router.search("...", provider=Provider.BRAVE)  # per-call override
```

`country` is a good illustration of the contract: Nimble maps it, and a backend
that does not raises `UnsupportedParameterError` rather than quietly running the
search unfiltered.

**Plain strings work anywhere a `Provider` does** — `WebRouter("nimble")` is the
same call. The enum covers the built-ins, so the string form is what names a
backend you added with `register_provider`, or one that arrives from a config
file, an environment variable or a CLI flag. Prefer the enum when you are
writing the name yourself; reach for a string when something else supplies it.

Searching and then reading the pages you found is the two methods together:

```python
response = router.search("latest nvidia earnings", max_results=5)
pages = router.extract(response.urls[:3], format="markdown")

for document in pages.documents:
    print(document.url, len(document.content))
```

Async works the same way:

```python
import asyncio
from web_router import AsyncWebRouter, Provider


async def main():
    async with AsyncWebRouter(Provider.NIMBLE) as router:
        response = await router.search("latest nvidia earnings", max_results=3)
        pages = await router.extract(response.urls, format="markdown")
        print(pages.urls)


asyncio.run(main())
```

### Runnable examples

[`examples/`](examples/README.md) has a script per topic — routing, every error
and its fix, depth measured across backends, adding your own provider — and
**most need no credentials**, routing through the keyless backends —
[`browser_session.py`](examples/browser_session.py) drives a local Chromium
with none. [`main.py`](examples/main.py) is a guided tour of search and extract
in one file:

```bash
uv run --env-file .env python examples/main.py
```

## Provider support

A provider name serves the methods it serves and no others: `brave` searches
and cannot extract, `oxylabs` extracts and cannot search, `nimble` searches,
extracts, runs templates and researches, and `browserbase` opens
sessions and runs browse tasks.
Ask a method for another's backend and it says so, naming the backends that
method does serve.

### Search

`[search]` installs all of these but AgentCore, which needs `[aws]`. The first column is what you pass to
`WebRouter(...)` to pick one at runtime. The string beside it is the same value,
and what `response.provider` and `configured_providers()` report back.

| Pass | Name | Provider | Backed by | Options mapped |
| --- | --- | --- | --- | --- |
| `Provider.NIMBLE` | `nimble` | [Nimble](https://nimbleway.com) | `nimble-python` | all of them |
| `Provider.TAVILY` | `tavily` | [Tavily](https://tavily.com) | `tavily-python` | `max_results`, `search_depth` |
| `Provider.YOU` | `you` | [You.com](https://you.com/platform) | direct HTTP | `max_results`, `search_depth` |
| `Provider.BRAVE` | `brave` | [Brave](https://brave.com/search/api/) | direct HTTP | `max_results` |
| `Provider.EXA` | `exa` | [Exa](https://exa.ai) | direct HTTP | `max_results` |
| `Provider.PARALLEL` | `parallel` | [Parallel](https://parallel.ai) | direct HTTP | `max_results` |
| `Provider.PERPLEXITY` | `perplexity` | [Perplexity](https://docs.perplexity.ai) | direct HTTP | `max_results` |
| `Provider.LINKUP` | `linkup` | [Linkup](https://www.linkup.so) | direct HTTP | `max_results`, `include_domains`, `exclude_domains` |
| `Provider.WEBIQ` | `webiq` | [WebIQ](https://api.microsoft.ai) (Microsoft AI Web Search) | direct HTTP | `max_results` |
| `Provider.KEENABLE` | `keenable` | [Keenable](https://docs.keenable.ai) | direct HTTP; keyless with `allow_keyless=True` (rate-limited per IP) | `max_results` |
| `Provider.DEEPSEEK` | `deepseek` | [DeepSeek Search](https://api-docs.deepseek.com/guides/search) | direct HTTP | `max_results` (at most 50), `include_domains`, `exclude_domains`, `locale`, the last three sent inside the query, which must fit 128 characters. `content` is DeepSeek's summary unless `provider_options={"need_content": True}` |
| `Provider.AGENTCORE` | `agentcore` | [AWS AgentCore Web Search](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-connector-web-search-tool.html), through your own AgentCore Gateway ([setup](docs/agentcore-setup.md)) | MCP over HTTP, SigV4 via `botocore` (`[aws]`) | `max_results` (at most 25), `include_domains`, `exclude_domains` (connector 1.2.0 or later). A date range is AgentCore's own `provider_options={"filters": {"publishedDateFilter": {"from": ..., "to": ...}}}`. Queries up to 200 characters. AWS's terms: show each result's citation, and do not store results in bulk |
| `Provider.YOU_MCP` | `you-mcp` | [You.com MCP](https://api.you.com/mcp) | direct HTTP, **no key** | `max_results`, `search_depth` |
| `Provider.EXA_MCP` | `exa-mcp` | [Exa MCP](https://exa.ai/docs/reference/exa-mcp) | direct HTTP, **no key** | `max_results` |
| `Provider.PARALLEL_MCP` | `parallel-mcp` | [Parallel Search MCP](https://docs.parallel.ai/integrations/mcp/quickstart) | direct HTTP, **no key** | `max_results` (applied to the response: the tool ignores a count) |
| `Provider.AUTO` *(default)* | `auto` | — | every backend you hold a key for, in the provider scan's order, then the keyless ones — so a key held only for research serves searches too; see [Working with no credentials](#working-with-no-credentials) | every option some member maps, each request routed to a member that maps what it sets |

Every provider but Nimble and Linkup is called with the minimum a search needs. Their APIs
can all do much more; mapping one of those options properly is a small PR and
the most useful thing to contribute — see
[CONTRIBUTING.md](CONTRIBUTING.md#promoting-a-provider-option).

> Brave, Exa, Parallel and Perplexity were added to broaden the catalogue and
> are intentionally minimal. Brave maps no `search_depth` because its API has no
> content-depth control at all. Perplexity's response mapping was verified live
> on 2026-11-03. For **Exa the endpoint and auth are verified, but the response
> mapping is taken from documentation and has not been confirmed against a real
> response** — no working key was available. Its integration tests exist and skip
> themselves until one is.

### Extract

`[extract]` installs all of these, and `configured_extract_providers()` reports
which ones this environment can use.

| Pass | Name | Provider | Credentials | Formats |
| --- | --- | --- | --- | --- |
| `Provider.NIMBLE` | `nimble` | [Nimble Extract](https://docs.nimbleway.com/api-reference/extract/extract) | `NIMBLE_API_KEY` | `markdown`, `html` |
| `Provider.TAVILY` | `tavily` | [Tavily Extract](https://tavily.com) | `TAVILY_API_KEY` *(keyless only through `auto`)* | `markdown`, `text` |
| `Provider.EXA` | `exa` | [Exa Contents](https://exa.ai) | `EXA_API_KEY` | `text` |
| `Provider.LINKUP` | `linkup` | [Linkup Fetch](https://docs.linkup.so) | `LINKUP_API_KEY` | `markdown`, `html` |
| `Provider.FIRECRAWL` | `firecrawl` | [Firecrawl Scrape](https://firecrawl.dev) | `FIRECRAWL_API_KEY`, or keyless with `allow_keyless=True` (capped per IP per day) | `markdown`, `html` |
| `Provider.OXYLABS` | `oxylabs` | [Oxylabs Web Scraper](https://oxylabs.io) | `OXYLABS_USERNAME` + `OXYLABS_PASSWORD` | `markdown`, `html` |
| `Provider.BRIGHTDATA` | `brightdata` | [Bright Data Web Unlocker](https://brightdata.com) | `BRIGHTDATA_API_TOKEN` + `BRIGHTDATA_ZONE` | `markdown`, `html` |
| `Provider.WEBIQ` | `webiq` | [WebIQ Browse](https://api.microsoft.ai) (Microsoft AI) — pages in WebIQ's own index | `WEBIQ_API_KEY` | `markdown`, `text` |
| `Provider.KEENABLE` | `keenable` | [Keenable Fetch](https://docs.keenable.ai/api-reference/fetch) | `KEENABLE_API_KEY`, or keyless with `allow_keyless=True` (rate-limited per IP) | `markdown` |
| `Provider.DEEPSEEK` | `deepseek` | [DeepSeek Fetch](https://api-docs.deepseek.com/guides/search) | `DEEPSEEK_API_KEY` | `markdown` |
| `Provider.JINA` | `jina` | [Jina Reader](https://jina.ai/reader) | **none** (`JINA_API_KEY` raises the rate limit) | `markdown`, `html`, `text` |
| `Provider.AUTO` *(default)* | `auto` | — | the original backends you hold a key for, then the keyless ones, then Linkup, Exa, WebIQ, Keenable and DeepSeek | `markdown` *(default)*, `html`, `text`, each routed to a member that returns it |
| `Provider.MAX` | `max` | — | the same members as `auto`, **all at once**; the first document back wins, and every fetch started is paid for -- see [The fastest answer any backend can give](#the-fastest-answer-any-backend-can-give-max) | as `auto` |

Two need more than a key. Oxylabs authenticates with the username and password
created under Web Scraper API in its dashboard — not the ones you sign in with —
and Bright Data needs the zone that routes the request beside its token. Neither
has a default worth guessing, so a missing one is an error naming the variable
to set.

> Nimble, Tavily, Jina, Linkup, Firecrawl, Oxylabs and Bright Data were verified
> live — the last two on 2026-11-03. **Exa's extract mapping is taken from
> documentation** and has not been confirmed against a real response; its
> integration tests skip themselves until a working key is available.

### Templates

A template is a vendor's saved, site-specific extraction: Nimble's Extract
Templates, Oxylabs' dedicated sources, Bright Data's scrapers, Apify's
actors. `[extract]` installs the Nimble adapter -- a template run is its
Extract API's. There is no keyless tier and no `auto`: a run fetches and
parses on the backend's account, and a template's name belongs to one
vendor, so `provider` is required on all three calls.

| Pass | Name | Provider | Credentials | Page formats beside the record |
| --- | --- | --- | --- | --- |
| `Provider.NIMBLE` | `nimble` | [Nimble Extract Templates](https://docs.nimbleway.com/api-reference/extract/extract-templates) | `NIMBLE_API_KEY` | `markdown`, `html` |

Nimble is the only backend with a template catalogue so far. Verified live on
2026-10-22: a listing, a description and a run on a public test page, the
response shapes of which the tests are modelled on. See
[Running a template](#running-a-template). Listing and describing bill
nothing; a run bills one template run on Nimble's account, at one flat rate
for every template, whatever the site -- the rate is on
[Nimble's pricing page](https://www.nimbleway.com/pricing), and
[`benchmarks/providers/PRICING.md`](benchmarks/providers/PRICING.md) records
it dated and sourced, from the price list `web_router.prices` reads (see
[Estimated prices](#estimated-prices-experimental)).

### Answer

`[answer]` installs these. One question, answered in one call: the backend
searches and writes the answer itself. No keyless tier, since every backend
bills the call; `auto` tries the ones you hold a key for, cheapest first. See
[Asking for an answer](#asking-for-an-answer) for what an unnamed call does.

| Pass | Name | Provider | Credentials | Options mapped | Citations tied to claims |
| --- | --- | --- | --- | --- | --- |
| `Provider.PERPLEXITY` | `perplexity` | [Perplexity Agent API, `fast` effort](https://docs.perplexity.ai/docs/agent-api/quickstart) | `PERPLEXITY_API_KEY` | `include_domains` or `exclude_domains` (not both), `country`, `recency` | yes: `[N]` markers, in `citation.field` |
| `Provider.YOU` | `you` | [You.com Answer API](https://you.com/docs/guides/answer.md) | `YOU_API_KEY` | `include_domains` or `exclude_domains` (not both), `country`, `recency` | yes: `[[1, 2]]` markers, in `citation.field` |
| `Provider.PARALLEL` | `parallel` | [Parallel Chat API (beta), `lite`](https://docs.parallel.ai/api-reference/chat-api-beta/chat-completions) | `PARALLEL_API_KEY` | none: the Chat API takes no source filter | no: its `basis` backs the answer as a whole |
| `Provider.EXA` | `exa` | [Exa Answer API](https://docs.exa.ai/reference/answer) | `EXA_API_KEY` | none: no source filter, and `userLocation` showed no clear effect | yes: `[N]` markers, in `citation.field` |
| `Provider.LINKUP` | `linkup` | [Linkup Search, `sourcedAnswer`](https://docs.linkup.so/pages/documentation/api-reference/endpoint/post-search) | `LINKUP_API_KEY` | `include_domains`, `exclude_domains`, `recency` | yes: `[N]` markers, in `citation.field` |
| `Provider.BRAVE` | `brave` | [Brave Answers API](https://brave.com/search/api/) | `BRAVE_ANSWER_API_KEY` (the Answers plan; not the search key) | none: no source filter, and `language` showed no effect | yes: each citation's span in the answer, in `citation.extra` |

All verified live on 2026-11-01, every mapped option seen to apply. The
medians and stated costs below are from the answer scan of 300 SimpleQA
questions on 2026-11-01, graded by `openai:gpt-4o`.

- `perplexity` answers in about two seconds (median 1.9 s over 300 questions) and cites inline as `[2]`; a
  citation whose marker appears in the answer carries it in `field`, and the
  response states its own cost under `raw[0]["usage"]["cost"]` (about $0.0012
  an answer over 300 questions). `country` is its `user_location`: an emergency-number question
  answered 999 for `GB` and 911 for `US`.
- `you` answers in about two seconds (median 1.7 s over 300 questions). Its `citations` are only the sources the
  answer cites, marked `[[1, 2]]`; each carries in `field` the whole marker
  group that first names it (`[[1, 2]]`, where other backends give one `[N]`), and its title comes from the pages You.com considered. It takes one
  domain list, not both: asking for both raises before the call. `language` was
  accepted but showed no effect, so `locale` is not mapped.
- `parallel` answers in about six seconds (median 6.3 s over 300 questions) with the `lite` model, the fastest
  whose `basis` ties its sources to the answer (`speed` returned none). Each
  citation carries the basis's `confidence` in `extra`; its reasoning stays in
  `raw`. Its Chat API is in beta and takes no source filter, so every option
  is refused.
- `exa` answers in about 1.3 seconds (median over 300 questions) and cites inline as `[1]`, numbering its
  citations from 1; it states its cost under `raw[0]["costDollars"]` ($0.005 an
  answer). Its Answer API has no source filter, so every option is refused.
- `linkup` answers in about a second (median 0.9 s over 300 questions) and lists every page it read; it is
  asked for inline citations, so the answer marks the ones it rests on as `[N]`, numbering them from 1, and a
  page read but never cited has no `field`. `recency` is its `fromDate`, and Linkup filters on the date it holds
  for a page, which is not always the publication date.
- `brave` answers in two to three seconds and was verified live on 2026-11-03, outside the scan. It returns its
  sources only on a streamed call, so the router streams it and reads the whole stream before answering. The
  answer is the text with Brave's `<citation>` and `<usage>` tags taken out; each tag is a citation, in order,
  with its `number` and its `start_index` / `end_index` into the answer in `extra`, and Brave writes no marker
  into the text, so `field` is unset. Its `<usage>` tag, in `raw`, states the call's cost: $0.054 for one
  question, almost all of it input tokens, about ten times the others. Every option is refused.

**Choosing one.** On 300 SimpleQA questions graded by one judge, all five land
at 94-95% and their intervals overlap, so accuracy does not separate them --
the [answer scan](benchmarks/providers/README.md#comparing-answer-backends)
reruns it. They differ in speed (Linkup, Exa, You.com and Perplexity in one to
two seconds, Parallel in about six), in the options they map (above), in
whether the answer ties each source to a sentence, and in cost: Perplexity and
Exa state theirs in the response; [`PRICING.md`](benchmarks/providers/PRICING.md)
lists the rest.

### Research

`[research]` installs all of these. Unlike the other two methods there is no
keyless tier: every agentic backend bills for the run, so `auto` here tries the
backends you hold a key for and then stops, naming the variables that would fix
it.

| Pass | Name | Provider | Credentials | Options mapped | Structured output |
| --- | --- | --- | --- | --- | --- |
| `Provider.NIMBLE` | `nimble` | [Nimble Web Search Agent](https://nimbleway.com) | `NIMBLE_API_KEY` | all of them | yes, and enrichment |
| `Provider.PARALLEL` | `parallel` | [Parallel Task API](https://parallel.ai) | `PARALLEL_API_KEY` | `effort`, `output_schema` | yes |
| `Provider.PERPLEXITY` | `perplexity` | [Perplexity Agent API](https://docs.perplexity.ai) | `PERPLEXITY_API_KEY` | `effort` | not mapped yet |
| `Provider.LINKUP` | `linkup` | [Linkup Research](https://docs.linkup.so) | `LINKUP_API_KEY` | `effort`, `output_schema` | yes |
| `Provider.YOU` | `you` | [You.com Research API](https://you.com/docs/guides/research) | `YOU_API_KEY` | `effort` | not mapped yet |
| `Provider.AUTO` *(default)* | `auto` | — | Nimble, Parallel, Perplexity, Linkup or You.com, whichever you hold, in that order | `effort`, `output_schema` | yes |

Nimble is the only backend web-router maps the whole surface for: source
guidance (`include_domains` / `exclude_domains`) and enrichment inputs
(`records`) are mapped for it alone so far. Parallel's Task API has a
[source policy](https://docs.parallel.ai/resources/source-policy) that is not
mapped yet. Ask another backend for either and it says so rather than running
without it.

Perplexity's and You.com's structured output is not mapped until a live run has
checked it, so neither declares `output_schema` — asking it for one raises
`UnsupportedParameterError` rather than handing back prose under the name of a
schema, which is the failure mode that quietly rots an agentic pipeline.

**Exa's Research API has been retired upstream** — every path answers `410 RESEARCH_RETIRED` — so web-router no longer ships an Exa research provider. Exa search and extract are unaffected.

How far each of the others has been verified, since it differs and the
difference matters:

| Provider | Verified how |
| --- | --- |
| `parallel` | **Live.** Real runs against the Task API, and every recorded payload in its tests is a real response. |
| `nimble` | **Against the `nimble-python` SDK's generated types**, produced from its OpenAPI spec — stronger than prose docs, weaker than a live call. |
| `perplexity` | **Live.** On 2026-09-30 the chat endpoint's deep research model answered 403 ("Sonar is now the Agent API"), so research moved to `/v1/responses`: a background run was followed from `queued` through `in_progress` to `completed`, presets `low`, `medium` and `xhigh` were accepted, and a cancel left a run `cancelled`. Its payloads are the ones in the tests. |
| `linkup` | **Live.** Runs at `effort="low"` (`reasoningDepth` S, $0.25 each) were followed from start to answer, one of them with an `output_schema`, whose result was the schema's object alone; their payloads are the ones in the tests. Efforts above `low` are documentation-derived. Linkup's docs put `max` (XL) at 10 to 20 minutes and `high` (L) at 5 to 10, longer than `research()`'s default 600-second wait: pass a larger `timeout`, or use `submit_research()` and poll, rather than leave a paid run going after the wait gives up. |
| `you` | **Live.** On 2026-10-16 a background run at `effort="low"` (`lite`) was followed from `queued` through `running` to `completed` on `api.you.com` (the same path on the search host answers 403); an unknown `research_effort` was a 422 naming `lite`, `standard`, `deep`, `exhaustive` and `frontier`. Its payloads are the ones in the tests. You.com offers no cancel, so `cancel()` returns `False`. `max` maps to `exhaustive`: `frontier`, background-only and priced well above it, is reachable as `provider_options={"research_effort": "frontier"}`. Efforts above `low` are documentation-derived. |

Each has an integration test that skips itself until a credential is there.

### Browser sessions and browse

`open_browser()` has five backends and `auto`, and `browse()` three and
`auto`. Their tables sit with the methods they serve, because each comes with
more to say than a row: see [Session backends](#session-backends) and
[Browse backends](#browse-backends).

### Working with no credentials

`auto` is the default for every method, so a router you never configured still
searches, extracts and opens a browser — the Quickstart's first example is exactly that. It tries the backends
you have a key for first — a credential in the environment says which one you
want — and falls back to the anonymous tiers when there is none: Tavily keyless,
then You.com's, Exa's and Parallel's open MCP servers, then Keenable's keyless tier for a search; Tavily keyless,
Jina Reader, Firecrawl's keyless tier, then Keenable's for an extract; `local` for a browser
session. Research and browse have no
anonymous tier, because every run bills; with no key they raise, naming the
variables that would fix it. `response.provider` names whichever one served it,
never `"auto"`, so you can always tell. The anonymous tiers are rate-limited, which is
why it is a chain: when one is throttled, the next answers.

Every backend you hold a key for is a candidate, in a fixed order:

| Method | Keyed members, in order | Then keyless | Then members added since, with a key |
| --- | --- | --- | --- |
| `search` | Perplexity, Nimble, Linkup, Tavily, Brave, WebIQ, Parallel, Exa, You.com | Tavily keyless, You.com MCP, Exa MCP, Parallel MCP, Keenable keyless | Keenable, DeepSeek, AgentCore |
| `extract` | Nimble, Tavily, Firecrawl, Oxylabs, Bright Data | Tavily keyless, Jina Reader, Firecrawl keyless, Keenable keyless | Linkup, Exa, WebIQ, Keenable, DeepSeek |
| `research` | Nimble, Parallel, Perplexity, Linkup, You.com | nothing — every run bills | — |

Search's order follows a provider scan's judged accuracy, best first (run your
own with [`benchmarks/providers/`](benchmarks/providers/README.md)), and every
key you hold comes before the free tier — so a key you hold only for research,
Perplexity's say, serves your plain searches too. Keenable, DeepSeek and AgentCore joined after that
scan, so they come after the keyless tier until a scan places them: with a gateway set up, a plain
`WebRouter().search()` is still served by a keyless member first, so name `Provider.AGENTCORE` to reach it. For extract, the backends that
joined later come after the keyless tier, so holding one more key never moves a
call that was already being served. Research has no free tier, so Linkup and then
You.com simply follow the others there. To choose your own order, pass a `chain`.

**`auto` routes each request to a member that can serve it.** It accepts every
option some member maps — the domain, country and locale filters through
Nimble, the domain filters through Linkup, the domain and locale filters through DeepSeek — and before trying a member it reads
that member's class, which costs no credential and sends nothing. A member that
does not map an option you set is passed over for that call: ask for
`search_depth` with keys for only Perplexity and Brave, neither of which has a
depth control, and the search goes to the keyless tier. When no member is left
it raises, naming what each one lacked, rather than running the query without
the option:

```python
router = WebRouter(provider_config={"auto": {"chain": {"search": ["perplexity", "brave"]}}})
router.search("...", search_depth="deep")
# UnsupportedParameterError: Provider 'auto' does not map 'search_depth'. No backend in
# its chain maps it, so nothing was searched: perplexity (does not map 'search_depth'),
# brave (does not map 'search_depth'). ...
```

Among the members that can serve the call, a failure — a missing key, a refused
request, a rate limit — moves on to the next, as it always has. So does an
empty answer: a search with no results and no answer, or an extract whose every
document is blank. When every member answers empty, you get the first empty
answer, not an error. A call that ends empty has tried every member that can
serve it, one after another, and each one you hold a key for bills for it. To
stop at the first answer, empty or not:

```python
router = WebRouter(provider_config={"auto": {"fallback_on_empty": False}})
```

That switch is the library's only; the MCP server always falls through.

For `extract` the same goes for the format, which every backend, `auto`
included, **pins** to `markdown` when you name none, rather than each applying
its vendor's own default. `html` passes over Tavily and Exa, and `text` goes to
Tavily, Exa or Jina Reader. A format is never relabelled: a member that cannot return it is
not asked.

#### Choosing the order yourself

`chain` in `provider_config["auto"]` replaces the default order. Keyed by
method, it sets that method's chain and leaves the others on their defaults:

```python
router = WebRouter(
    provider_config={
        "auto": {
            "chain": {
                "search": ["perplexity", "nimble", "brave", "you-mcp"],
                "extract": ["exa", ("tavily", {"allow_keyless": True}), "jina"],
            }
        }
    }
)
```

Each member is a name, or `(name, kwargs)` for one that needs its own settings.
A member is built with its own `provider_config` entry, the chain's kwargs laid
over it, so a key passed in code reaches it through `auto` exactly as it does by
name. One backend first and another when it fails is a chain of two:

```python
router = WebRouter(
    provider_config={
        "nimble": {"api_key": nimble_key},
        "firecrawl": {"api_key": firecrawl_key},
        "auto": {"chain": {"extract": ["nimble", "firecrawl"]}},
    }
)
router.extract(url)  # Nimble; Firecrawl when Nimble fails. response.provider says which.
```

The chain replaces the default whole, keyless tail included, and a member still
needs its key. `provider_config["auto"]` reaches the `auto` of every method, so
a chain given as a plain list is every method's chain — key it by `"search"`,
`"extract"`, `"research"`, `"browser"` or `"browse"` unless every name in it
serves each method you call through `auto`. The
[provider scan](benchmarks/providers/README.md) prints a suggested search
order in this form, from its own judged accuracy.

This applies **only when you name no provider at all**. Ask for a provider and a
missing key is still an error naming the variable to set — a named backend is
never quietly demoted to an anonymous one:

```python
WebRouter(Provider.TAVILY).search("...")  # ConfigurationError if TAVILY_API_KEY is unset
```

## How it works

| Piece | Role |
| --- | --- |
| `WebRouter` / `AsyncWebRouter` | The facade you call. Holds provider config and routes each call. |
| `SearchProvider` / `ExtractProvider` | The contract a backend implements, one per method: `_search`/`_asearch`, `_extract`/`_aextract`. |
| `SearchRequest` / `ExtractRequest` | A normalized call, before any provider sees it. |
| `SearchResponse` / `SearchResult` | A normalized answer, plus the provider's untouched payload in `raw`. |
| `ExtractResponse` / `ExtractedDocument` | The same for a page fetch, plus `failures` for the URLs that did not come back. |
| `TemplateProvider` | The contract for an Extract Template backend: `_list` its catalogue, `_get` one template, `_run` it. |
| `TemplateRequest` / `TemplateResponse` | A normalized run -- a template's name and its own inputs -- and the record it parsed, in `data`. `Template` describes one, `TemplatePage` a page of the catalogue. |
| `ResearchProvider` | The contract for an agentic backend: `_start` a run, `_fetch` where it got to. |
| `ResearchRequest` / `ResearchResponse` | A normalized run, and its answer — prose, structure, and the `citations` behind either. |
| `ResearchJob` | A run in progress. Polls with back-off, blocks with a timeout, and never fabricates an answer it does not have. |
| `BrowseProvider` / `BrowseJob` | The same job shape for a hosted browser agent: `BrowseRequest` in, `BrowseResponse` — answer, structure, `steps` — out. Research and browse share one job core. |
| `BrowserProvider` | The contract for a browser session backend: `_open` a session, `_release` it. |
| `BrowserHandle` / `AsyncBrowserHandle` | An open session and its `cdp_url`, with the page actions, released when the `with` block ends however it ends. |

Four rules keep the abstraction honest:

**Unset stays unset.** Fields you leave alone are not sent, so each backend
applies its own defaults rather than inheriting ours. (Three deliberate
exceptions: an extract sends a format even when you named none, `markdown`
wherever the backend can return it — so the same call answers alike whichever
backend serves it, and `document.format` is true because it was asked for, not
because a vendor default has not moved. A
browser session opened with no `timeout` gets ten minutes rather than the
vendor's own limit, because it is billed until something ends it. And `local`
is headless unless you say otherwise, because there is no vendor default to
keep and a window would open on your screen.)

**Nothing is silently dropped.** If a provider does not map an option you set —
or cannot return the `format` you asked for — it says so instead of quietly
doing something weaker:

```python
WebRouter(Provider.TAVILY).search("...", country="US")
# UnsupportedParameterError: Provider 'tavily' does not map 'country'. Pass it
# through provider_options if the underlying API supports it, or add the
# mapping -- see CONTRIBUTING.md.
```

**Nothing is thrown away.** Unmapped provider fields land in `SearchResult.extra`
or `ExtractedDocument.extra`, and the complete payloads in `raw`, so
normalization is never lossy.

**Nothing fails quietly.** An extract over several URLs reports the ones that
did not come back in `failures` and returns the rest; a call that produced no
document at all raises. An empty answer is never something you have to go
looking for.

## Search options

```python
response = router.search(
    "nvidia q3 earnings",
    max_results=10,
    search_depth="fast",  # "lite" | "fast" | "deep"
    include_domains=["reuters.com", "bloomberg.com"],
    exclude_domains=["example.com"],
    country="US",
    locale="en",
)
```

Every option except `query` is optional. Which ones a given provider maps is in
the support table above; setting one it does not map raises rather than being
ignored.

That is the whole surface. It is deliberately small: these are the options that
mean the same thing on every backend, so code written against them keeps working
when you switch providers.

### Search depth

`search_depth` is the one option worth understanding, because providers describe
content richness in incompatible vocabularies. web-router reduces it to three
levels and maps each onto the backend's own knobs:

| Depth | What you get | Nimble | Tavily | You.com | You.com MCP |
| --- | --- | --- | --- | --- | --- |
| `lite` | Title, URL, snippet | `lite` | `basic` | no extraction | `none` |
| `fast` | A truncated page body | `standard` | `advanced` | `highlights` | `highlights` |
| `deep` | Full page content | `standard` + `full_content` | `advanced` + raw content | `full_page` | `full_page` |

`result.content` always holds the fullest text available. Some backends return
the body in the field others use for the snippet; that is normalized away, so
`content` is the field to read whatever the backend did.

`result.description` is the provider's snippet **when it returns one as a
separate field**. Not every backend does at every depth — Nimble at `fast`
returns the page body and no snippet, so `description` holds that body and is
identical to `content`. Read `description` when you want the short form and
have checked your backend supplies one; read `content` when you want text.

**Linkup refuses `search_depth`**: its own `depth` is how hard it works to
retrieve, not how much of a page it returns. Measured live on 2026-09-30, its
`flash`, `fast`, `standard` and `deep` all answered with snippets of the same size
per result, `flash` in about half a second and `deep` in six to sixteen, and its
docs bill `deep` at ten times the others ($0.05 a search against $0.005). Mapping
`search_depth="deep"` onto it would charge ten times more for no more text, so a
search sends Linkup's recommended `fast` and its depths are reachable as
`provider_options={"depth": "flash" | "standard" | "deep"}`.

## Extracting page contents

`extract()` is the other half: give it URLs you already have and it returns the
pages, through whichever backend you point it at.

```python
response = router.extract(
    ["https://example.com", "https://www.iana.org/domains/reserved"],
    format="markdown",  # "markdown" | "html" | "text"
)

for document in response.documents:
    print(document.url, document.format, len(document.content))
```

One URL or many — `router.extract("https://example.com")` is the same call, and
the answer has the same shape. Backends that take a single URL per request are
fanned out for you, concurrently on `AsyncWebRouter`, and the documents come back
in the order you asked for them.

### Formats

`format` is the extract equivalent of `search_depth`: three names for what the
page should come back as, mapped onto each backend's own vocabulary.

| Format | What you get | Nimble | Tavily | Firecrawl | Oxylabs | Bright Data | Jina | Linkup |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `markdown` | Structure kept, markup dropped | `formats: [markdown]` | `format: markdown` | `formats: [markdown]` | `markdown: true` | `data_format: markdown` | `X-Return-Format: markdown` | always returned |
| `html` | The page's HTML as fetched | `formats: [html]` | — | `formats: [html]` | default (`type: raw`) | default | `X-Return-Format: html` | `includeRawContent: true` |
| `text` | Visible text, no markup | — | `format: text` | — | — | — | `X-Return-Format: text` | — |

Exa is left out of the table because it has one row to give: its Contents API
returns `text` and nothing else, asked for as `text: true`. WebIQ is left out
too: Browse takes `contentFormat: markdown` or `text`, and its `html` is a
simplified markup rather than the page as fetched, so `html` is refused. It
reads WebIQ's own index, so a page WebIQ has not crawled comes back as a
failure. Keenable is left out as well: its Fetch API has no format field and
returns markdown alone, so `html` and `text` are refused. web-router asks it for
up to 500,000 characters, since it otherwise cuts a page at 50,000. DeepSeek is
left out too: its Fetch API returns markdown alone.

A backend that cannot produce the format you asked for **raises rather than
returning a different one under the same name**, because code that pipes
`document.content` into a markdown parser has no way to notice HTML:

```python
WebRouter(Provider.TAVILY).extract("https://example.com", format="html")
# UnsupportedFormatError: Provider 'tavily' cannot return 'html' content; it
# returns 'markdown', 'text'. Ask for one of those, name a provider that returns
# 'html', or add the mapping -- see CONTRIBUTING.md.
```

`document.format` always says which representation `document.content` holds.
Leave `format` unset and you get `markdown` from every backend that can return
it, Nimble, Oxylabs and Bright Data included, whose own APIs default to HTML:
an agent reading the page pays for every character. Exa, which returns only
text, returns `text` and says so. Ask for `html` by name to get the markup. `auto` takes all three formats and sends each call only to a
member that returns the one asked for.

### Partial failure

Extracting several URLs can partly succeed, and it says so rather than pretending
otherwise. What came back is in `documents`; every URL that did not is in
`failures` with the reason:

```python
response = router.extract(["https://example.com", "https://nope.invalid"])

response.urls  # ['https://example.com/']
response.failures[0].url  # 'https://nope.invalid'
response.failures[0].error  # why it did not come back
```

A call where **nothing** came back raises instead, so a bad credential can never
look like a page with no text. This works the same way whether the backend
reports its own per-URL failures (Tavily, Exa) or was fanned out one URL at a
time (everything else) — that uniformity is enforced in `ExtractProvider`, not
asked of each provider.

`response.raw` holds the untouched payloads, one per API call that answered: a
single entry from a backend that took every URL at once, one per URL that came
back from a backend that did not. A call that failed outright is in `failures`
instead — there was no payload to keep.

### The fastest answer any backend can give: `max`

`auto` tries its backends one after another and stops at the first that works.
`max` sends the same request to **every backend at once** and returns the first
document that comes back:

```python
response = WebRouter(Provider.MAX).extract(url, format="markdown")
response.provider  # whoever answered first -- never 'max'
```

It is for the caller who wants the fastest working extract and is willing to
pay for it: every fetch `max` starts is billed by its backend, so a call costs
up to one extract **per configured backend per URL**. Most backends take one
URL per request, and a loser keeps fetching the URLs it has not reached after
the winner has returned, so ten URLs through three backends can bill thirty
fetches where `auto` bills ten. That is why it is never the default and has to
be named. The members are exactly the ones `auto` would try, in the
same order, chosen the same way: a `chain` set under `"auto"` in
`provider_config` narrows `max` too, so a paid backend taken out of one is out
of both, and `max` races a different list only when its own `"max"` entry
names one. `format` routes the same way, to the members that can return it.

Only a document wins. A backend that refused the fetch, could not be built or
answered with nothing is that backend's loss, not the call's, and when every
backend loses the error names each one's reason. What a slower backend would
have returned is never judged: the first document is the answer, which is the
"never invent data" rule applied to a race. With several URLs it is the first
*complete* answer: a backend that fetched part of the list and failed the rest
is held while another can still answer, and returned, `failures` and all, only
when none can do better. On the sync router the losers finish on their own
threads and `close()` waits for them before shutting their clients; on the
async router they are cancelled.

**`hedge` keeps the cost down.** With a hedge, the first member runs alone for
that many seconds and the rest start only if it has not answered by then:

```python
router = WebRouter(Provider.MAX, provider_config={"max": {"hedge": 0.8}})
```

A page the first backend returns within the hedge costs one fetch; only the
slow ones pay for the race. A first member that fails, or answers in part,
before the hedge runs out starts the rest at once. Unset, every member starts
together. The hedge covers the first member's whole call, however many URLs it
carries: it caps the cost of a one-URL request, not of a list, since a first
member slower than the hedge on a list of URLs starts every other member on
the whole list.

`max` is left out of `benchmark()`'s default set as `auto` is: a row for it
would repeat one of the others under a name that says nothing about who
answered.

## Running a template

A template is a vendor's saved, site-specific extraction -- a retailer's
product page, a job board's listing, a search results page -- that turns one
page into a **record with fixed fields** rather than page text to read through.
Three calls cover a vendor's catalogue of them: list it, describe one, run one.
All three name the vendor, because a template's name, inputs and fields are
the vendor's own; the same page has a different name on each.

```python
page = router.templates(provider="nimble", limit=20)  # one page of the catalogue
for template in page.templates:
    print(template.name, template.domain, template.entity_type)

template = router.template("walmart_pdp", provider="nimble")
template.input_schema  # JSON Schema of what a run passes as `inputs`
template.output_schema  # JSON Schema of the record that comes back

response = router.run_template("walmart_pdp", {"product_id": "123456789"}, provider="nimble")
response.data  # {'title': ..., 'price': ..., ...}, shaped by output_schema
```

**A template's inputs are its own, not ours.** `inputs` is whatever its
`input_schema` names: a URL for some templates, an id, a slug or a search term
for others, and nothing at all for a template whose page is fixed. That is why
this is a method of its own rather than an option on `extract`, which is keyed
by URL. Read the schema -- or `template.samples`, the runs the backend
recorded, each an input and the record it returned -- before running a
template you have not run before; the backend refuses inputs that do not
match, and an unknown name raises `TemplateNotFoundError`. A listing carries
the same: Nimble's catalogue rows hold the published version whole, so
`page.templates[i].input_schema` and `.samples` are filled without a second
call.

`format` asks for the page beside the record, as `markdown` or `html`:

```python
response = router.run_template("walmart_pdp", {"product_id": "123456789"}, provider="nimble", format="markdown")
response.document.content  # the page the record was parsed from
```

Unset, no page comes back -- the record is the answer and the page is an
extra, so unlike `extract` there is no default format to pin. A run the
backend could not complete, whether it could not fetch the page, the site
answered with an error page, or the template failed to parse it, **raises**
`ProviderError` with the backend's reason rather than coming back with an
empty record.

**One call is one page and one set of inputs**, answered in real time with
one record: a listing template returns its items inside that record, with
pagination beside it. A vendor whose templates run as jobs over many inputs
and answer with a dataset -- Bright Data's scrapers, Apify's actors -- would
join through its synchronous endpoint, under that endpoint's own time cap;
batch inputs are out of scope here.

`TemplatePage.total` and `offset` say where in the catalogue a page sits, when
the backend reports them; `limit` and `offset` left unset leave the paging to
the backend. Every call tags its answer with `provider` and keeps the untouched
payload in `raw`.

## Asking for an answer

`answer()` asks one question and returns the backend's own written answer with
the sources it rests on, in one call. It sits between `search()`, which returns
the pages, and `research()`, a job of minutes.

Three steps to a first answer:

1. Install with the `answer` extra: `pip install -e "/path/to/webrouter[answer]"`
   (`make setup` in a clone installs every extra).
2. Set one answer backend's key in your environment or `.env`, for example
   `PERPLEXITY_API_KEY` -- the [answer table](#answer) lists each one's variable.
3. Ask:

```python
from web_router import WebRouter

with WebRouter() as router:
    response = router.answer("who won the 2022 world cup final")  # the one backend you hold a key for

print(response.answer)
for citation in response.citations:
    print(citation.field or "-", citation.url)
```

```text
Argentina won, beating France 4-2 on penalties after a 3-3 draw through extra time.[2]
- https://football.fandom.com/wiki/2022_FIFA_World_Cup_Final
[2] https://www.world-cup-schedule.com/blog/who-won-2022-world-cup-argentina-france-final
...
```

With more than one key set, name the backend: `router.answer(q, provider="linkup")`.
Which one: they score alike on accuracy (see [Choosing one](#answer)), so pick
on what you need -- Linkup had the lowest median latency in that scan (0.9 s); Perplexity, You.com
and Linkup take source filters (domains, recency); Perplexity, You.com, Exa and
Linkup tie each source to a sentence. [`examples/answer.py`](examples/answer.py)
runs the whole flow, errors included.

Nothing here writes, joins or edits the answer: it is the backend's, as it came.
An empty one comes back as `""` rather than an error, with its sources and
`raw`, so "the backend had nothing" reads differently from a failure. Where a
backend marks which source backs which sentence, the marker is in
`citation.field`; where it does not (Parallel), every citation backs the answer
as a whole. `outcome` is `partial` only when the backend says it stopped early.

The options are `include_domains`, `exclude_domains`, `country`, `locale` and
`recency` (`"day"`, `"week"`, `"month"` or `"year"`). Windows count whole days;
where the router sets the start date itself (Linkup), it counts back from
today's date in UTC. A backend that does not
map one raises `UnsupportedParameterError` before anything is sent -- the
[answer table](#answer) says which backend maps what.

With `provider` unset, the router's default answers when it is an answer
backend. On the usual default, `auto`, that is every answer backend you hold a
key for (in the environment or in `provider_config`), cheapest first at list
price: Perplexity, Exa, You.com, Linkup, Parallel, then Brave. A backend that does not
map an option you set is passed over without being called; one that fails, or
answers with nothing, is passed over for the next, and each one tried bills.
`provider_config={"auto": {"chain": {"answer": [...]}}}` sets the order. With no
key, the call raises `NoAnswerProviderError` (a `ProviderNotFoundError`) naming
the variables to set. On a router whose default is not an answer backend
(`WebRouter("nimble")`), the one answer backend you hold a key for serves the
call; with several, name one, or pass `provider="auto"`.

"Answer" means two other things nearby. `SearchResponse.answer` is an answer a
search backend volunteered beside its results, unasked (You.com's knowledge
panel); `answer()` is one you asked for, with its sources.

## Researching a question

`research()` behaves differently enough to be worth a section. A search answers in a second. A research run reasons over
several turns — searching, reading, cross-checking — and answers in minutes.

```python
response = router.research("how did nvidia's datacenter revenue mix shift in 2025")

print(response.answer)
for citation in response.citations:
    print(citation.url, citation.field or "")
```

The line between this and `search` is not "does it reason". It is **whether the
work fits inside one request/response**. A written answer that comes back in
one call is `answer()`. A run that
takes minutes, can be polled, can be cancelled and bills by the unit of effort is
a job, and it needs a different shape.

### Asking for structure instead of prose

Pass a schema and the answer comes back as an object. Either JSON Schema or the
pydantic model to take one from:

```python
class Company(BaseModel):
    name: str
    what_they_sell: str
    headquarters: str | None = None


response = router.research("what does nimbleway sell", output_schema=Company)
response.structured  # {'name': 'Nimble', ...}
```

Add `records` on top of a schema and the run becomes an **enrichment**: the same
schema, filled in once per row you already hold: `structured` comes back as one
row per record. Nimble, the backend that maps it, refuses one at `effort="low"`;
its default effort runs one, as the example below does (checked live).

```python
router.research(
    "fill in what each company sells and where it is headquartered",
    output_schema=Company,
    records=[{"name": "Nimble"}, {"name": "Tavily"}],
)
```

### Effort

`effort` is research's equivalent of `search_depth` — four levels mapped onto
each backend's own vocabulary, which disagree more here than anywhere else in
the package:

| Effort | Nimble | Parallel | Perplexity | Linkup | You.com |
| --- | --- | --- | --- | --- | --- |
| `low` | `low` | `lite` | `low` | `S` | `lite` |
| `medium` | `medium` | `core` | `medium` | `M` | `standard` |
| `high` | `high` | `pro` | `high` | `L` | `deep` |
| `max` | `max` | `ultra` | `xhigh` | `XL` | `exhaustive` |

A backend with fewer tiers maps more than one level onto the same setting rather
than inventing a distinction it does not have. Nimble's `x-high` and `5x-high`, and Parallel's `base`,
have no normalized level of their own and are reachable through
`provider_options`, as is You.com's `frontier`.

Parallel is the one backend that *requires* an effort, so leaving it unset sends
`medium` rather than omitting the field — the same deliberate exception to
"unset means unset" that `extract` makes for `format`, and for the same reason:
the alternative is a value that depends on a vendor default nobody declared.

### Not waiting for it

`research()` blocks, which is wrong anywhere a minutes-long wait is — a request
handler, a UI, an MCP server. `submit_research()` hands back the job instead:

```python
job = router.submit_research("...", effort="high")

job.job_id  # the backend's own id for the run
job.status()  # where it is now, without blocking
job.result(timeout=30)  # block for a while; raises ResearchTimeoutError if it is not done
```

Running out of time **raises rather than returning an empty answer**, and leaves
the run going rather than cancelling it — the exception carries the job id, so
picking the same run back up costs nothing where starting it again would cost
the whole run. `job.cancel()` stops one where the backend supports it, and
returns `False` where it does not rather than pretending.

The async router is the same call with `await`, which is what makes several
research runs at once reasonable there and not on the sync one.

### What comes back

```python
response.answer  # prose, when the run produced prose
response.structured  # the object, when a schema was asked for
response.citations  # what the answer rests on
response.outcome  # 'complete', or 'partial' when the backend said it stopped early
response.provider  # who actually answered -- never 'auto'
response.raw  # every untouched payload the run took
```

`Citation.field` says **which part of the answer a source backs** — a JSON path
into `structured`, or a marker in `answer` — for the backends that attribute per
field. `None` means it cites the answer as a whole.

`outcome` is `"partial"` only when the backend itself reported stopping early:
an exhausted budget, a truncated generation. A run that simply found little is
`"complete"`, because it finished the work it was asked to do. Nothing here is
inferred.

## Browser sessions

`open_browser()` is the one method that returns neither an answer nor a job but
a **resource**: a live browser, and a handle on it. Drive it with the handle's
own page actions, or hand its `cdp_url` to Playwright, browser-use or
Stagehand. A cloud session bills for as long as it is open, so the handle is a
context manager that releases it however the block ends:

```python
from web_router import WebRouter

with WebRouter().open_browser(timeout=300) as browser:
    browser.goto("https://example.com")
    print(browser.provider, browser.snapshot().tree)
# released here -- on an exception too, and on router.close()
```

A session opened with no `timeout` gets `BROWSER_TIMEOUT`, ten minutes, rather
than the vendor's own limit, which on some backends is hours: a session you
forgot to release should cost ten minutes of credit, not an afternoon's. Pass a
larger `timeout` to go past it. Releasing twice is a no-op, and a handle
garbage-collected while still open warns with `ResourceWarning`.

**Connect once per session.** Several backends — Browserbase and Hyperbrowser
among them — end a session when its CDP client disconnects, so a driver that
reconnects for each action finds the session gone after the first. The handle's
own actions already work this way: the first one connects, and the rest reuse
that connection until the handle is released.

`cdp_url` is a credential — whoever holds it drives the browser, and Steel puts
your API key in it — so `BrowserSession.cdp_url` is a `SecretStr`, and neither
the session's `repr` nor its serialization carries it or the raw payload.
`browser.cdp_url` returns the plain string, because asking for it by name is the
one moment you need it.

### Session backends

Opening a session on a vendor goes over its REST API with httpx, which `[all]`
installs; `local` needs only the standard library and a Chrome or Chromium. The
page actions need the `[browser]` extra, which is Playwright.

| Pass | Name | Provider | Credentials | Options mapped | Verified how |
| --- | --- | --- | --- | --- | --- |
| `Provider.AUTO` *(default)* | `auto` | — | whatever its chain holds; none for `local` | every option some member maps, routed per request; `provider_options` is refused | Falls through only when a member refuses to open (no key, a 4xx, no connection); anything that may have opened a session is raised |
| `Provider.STEEL` | `steel` | [Steel Sessions](https://docs.steel.dev) | `STEEL_API_KEY` | `timeout`, `idle_timeout`, `solve_captchas`, `proxy_country` (no enumerated country list; `stealth` not mapped — Steel has no on/off or tiered stealth field), `profile` (an id), `persist_profile` | `profile`, `persist_profile` and `control_url()` **live**, 2026-10-20; the rest from documentation — `solve_captchas`/`proxy_country` await a live run |
| `Provider.BROWSERBASE` | `browserbase` | [Browserbase Sessions](https://docs.browserbase.com) | `BROWSERBASE_API_KEY` | `timeout`, `stealth` (`low`/`high`, via `browserSettings.verified` — `advancedStealth` is deprecated), `solve_captchas`, `proxy_country`, `profile` (a context id), `persist_profile` | `profile`, `persist_profile` and `control_url()` **live**, 2026-10-20; the rest from documentation — `stealth`/`solve_captchas`/`proxy_country` await a live run |
| `Provider.HYPERBROWSER` | `hyperbrowser` | [Hyperbrowser Sessions](https://hyperbrowser.ai/docs) | `HYPERBROWSER_API_KEY` | `timeout` (rounded up to whole minutes), `stealth`, `solve_captchas`, `proxy_country`, `profile` (an id), `persist_profile` | `profile`, `persist_profile` and `control_url()` **live**, 2026-10-20; the rest from documentation — `stealth`/`solve_captchas`/`proxy_country` await a live run |
| `Provider.ANCHOR` | `anchor` | [Anchor Browser](https://docs.anchorbrowser.io) | `ANCHOR_API_KEY` | `timeout`, `idle_timeout` (rounded up to whole minutes), `stealth`, `solve_captchas`, `proxy_country`, `profile` (a name), `persist_profile` | `profile`, `persist_profile` and `control_url()` **live**, 2026-10-20; the rest from documentation — `stealth`/`solve_captchas`/`proxy_country` await a live run. Headful and recorded by default, and left that way |
| `Provider.KERNEL` | `kernel` | [Kernel Browsers](https://kernel.sh/docs) | `KERNEL_API_KEY` | `idle_timeout` (whole seconds, 10 s to 72 h), `stealth` (one tier, which also brings Kernel's CAPTCHA solver and proxy), `headless`, `viewport`, `profile` (an id), `persist_profile`. `timeout`, `solve_captchas` and `proxy_country` raise | **Live**, 2026-10-20: open, drive, status, release, `control_url()`, and every mapped option |
| `Provider.LOCAL` | `local` | Google Chrome or Chromium on this machine | none (`CHROME_PATH` optional) | `headless`, `timeout`, `viewport` | **Live** against Google Chrome on macOS: launch, CDP `/json/version`, release, lifetime expiry, exit cleanup. `headless=False` is not exercised by a test |

`auto` opens through the first vendor you hold a key for, in the order **Steel,
Browserbase, Hyperbrowser, Anchor, Kernel**, and then **`local`** — a key in the
environment says which backend you want, so `local` is the fallback, never a
substitute for a vendor you are paying for. Each member is checked against the
request on its class before anything opens, so a member that does not map an
option you set is passed over: `idle_timeout` skips Browserbase, Hyperbrowser
and `local`, `timeout` skips Kernel, and `headless` every vendor but Kernel. The ten-minute default does not count as an
option you set. `provider_options` is refused, because it is one backend's own
fields and `local` would read it as Chrome switches — name the backend you
wrote it for. `profile` and `persist_profile` are refused for the same reason:
a profile is saved on one backend. `auto` never opens a second session in one call: it moves on only
after a failure that proves nothing opened, and a read timeout, a 5xx or an
unreadable answer, which may have left a session billing, is raised instead.
The handle it returns is the member's own, so `browser.provider` names the
backend that served it.

`stealth` is a level, not a switch: `"off"`, `"low"` (the backend's standard
stealth) or `"high"` (its strongest, often plan-gated), and unset leaves the
backend's own default. A backend without the level you ask for raises
`UnsupportedParameterError` naming it rather than serving another one, and
`auto` passes such a member over, as it does one that serves an option only
beside another you left unset. Hyperbrowser, Browserbase, Anchor and Kernel map it; Steel does not.

`provider_options` on a named backend merges beneath the mapped options, and
setting a field a mapped option also sets — including a `timeout` filled in
from the default — raises `ConfigurationError` rather than one side silently
winning.

### Logging in once, and reusing the login

A site behind a login needs a person to log in once. `control_url()` hands back
a page where that person sees the session and can type into it; with `profile`
and `persist_profile=True`, what they leave behind — cookies, local storage —
is saved when the session is released, and a later session opened on the same
`profile` starts logged in:

```python
from web_router import WebRouter

with WebRouter("browserbase") as router:
    with router.open_browser(profile="<context id>", persist_profile=True) as browser:
        print("Log in here:", browser.control_url())
        input("Press Enter once you are logged in. ")
    # Released: the login is saved to the profile. Give it a few seconds.

    with router.open_browser(profile="<context id>") as browser:
        browser.goto("https://example.com/account")  # already logged in
```

`control_url()` is a credential, like `cdp_url`: whoever opens it drives the
browser, and on Steel, Kernel, Hyperbrowser and Anchor no account login stands
in the way. Give it only to the person logging in, and do not log it. It is
`None` on a backend that offers no such page. `live_view_url` stays for pages
that show a session without granting control — today Steel's dashboard page,
which asks for a Steel login. Browserbase hands out its page only on request,
so `control_url()` asks for it the first time it is called; every other
backend returned it on open. On an `AsyncBrowserHandle`, `await
browser.control_url()`.

| Backend | `profile` is | `control_url()` is | Make a profile |
| --- | --- | --- | --- |
| `browserbase` | a context id | `debuggerFullscreenUrl`, fetched on first call | `POST /v1/contexts` |
| `steel` | a profile id | `debugUrl` | open a session with `provider_options={"persistProfile": True}` and read `raw["profileId"]` |
| `hyperbrowser` | a profile id | `liveUrl` | `POST /api/profile` |
| `anchor` | a name you choose | its `live_view_url` | none: the first session that saves to a name creates it |
| `kernel` | a profile id | `browser_live_view_url`, for a session with `headless=False` | `POST /profiles` |
| `local` | — | `None`: open it with `headless=False` and the window is on your screen | — |

web-router takes a profile you already have; making, listing and deleting them
stays with each vendor's own API or dashboard. `persist_profile` without a
`profile` raises, rather than making a new one where the vendor would (Steel).
Each backend saves as the session ends, not at the release call, so wait a few
seconds before opening on the profile again; Steel answers 409 until its upload
reports `READY`. Two sessions writing to one profile at once overwrite each
other. Every row was checked live on 2026-10-20: a cookie and a localStorage
entry set in one session were there in the next.

### Driving the page

A handle can drive its session's page itself, the same way whichever backend
serves it: the actions run over the session's CDP URL through Playwright, once
for every backend, never through a vendor's own endpoints. They need the
`[browser]` extra, and nothing else — no `playwright install`, because the
browser is the session's.

```python
from web_router import WebRouter

with WebRouter("local").open_browser() as browser:
    browser.goto("https://news.ycombinator.com")  # PageState: url, title, status_code
    page = browser.snapshot()  # PageSnapshot: url, title, tree
    print(page.tree)
    # - link "Hacker News" [ref=e6]
    # - textbox [ref=e312]
    browser.click(ref="e6")  # or selector="a.morelink"
    browser.type("web-router", ref="e312", submit=True)
    browser.press("Escape")
    png = browser.screenshot(full_page=True).png  # Screenshot: png, width, height, url
    text = browser.read().content  # ExtractedDocument; format="text" or "html"
```

`snapshot()` returns the page's accessibility tree, one element per line, each
tagged with a ref to pass to `click` or `type`. Unnamed wrapper elements and
link URLs are left out to keep it short. Treat a ref as opaque: it is good
until the next snapshot or navigation, and one used after that, or after its
element was re-rendered, raises `StaleElementError` rather than clicking
whatever now sits there. A real page's tree runs to tens of thousands of
characters; `snapshot(max_chars=...)` cuts it at a line boundary and sets
`truncated`, and the refs past the cut are not offered. A `Screenshot`
serializes to JSON with its PNG as base64.

Every action waits up to `BROWSER_ACTION_TIMEOUT` (30 seconds) unless you pass
`timeout=`, except `press`, which waits on nothing. Sync handles on one thread
share one Playwright, each with its own connection. The sync handle refuses to
run inside an event loop, because Playwright's sync API does; use
`AsyncWebRouter` there, whose handle has the same actions, awaited:

```python
async with AsyncWebRouter("local") as router, await router.open_browser() as browser:
    await browser.goto("https://example.com")
    page = await browser.snapshot()
```

Failures raise a `BrowserActionError`, carrying the `provider`, `session_id`
and `action`, with Playwright's error on `__cause__` — or one of its
subclasses, so an agent can tell them apart: `StaleElementError`,
`ElementNotFoundError` (a selector matched nothing in time), `NavigationError`
(`goto` could not load the page), `BrowserActionTimeoutError` and
`PageClosedError` (the page, the connection or the session is gone).

[`examples/browser_session.py`](examples/browser_session.py) runs all of this
against `local`, sync and async, with no key.

### Session backend notes

**Steel.** Its `cdp_url` carries your API key in its query string, which is
Steel's own connection scheme. It keeps a session across a reconnect, as
`local` does. Proxies, stealth, CAPTCHA solving, profiles, viewport and
headless are reachable through `provider_options` until a live call confirms a
mapping for them. `profile` and `persist_profile` map onto `profileId` and
`persistProfile`. `live_view_url` is its `sessionViewerUrl`, a dashboard page
that asks for a Steel login; its `debugUrl` needs none, so it is
`control_url()`.

**Browserbase.** Its `cdp_url` is the `connectUrl` it returns, which is enough
on its own to drive the session. It solves CAPTCHAs unless
`browserSettings.solveCaptchas` is set to `false` through `provider_options`;
it ends a session when its CDP client disconnects, unless `keepAlive` is set;
and it has no idle timeout, so `idle_timeout` is refused. It reports a session
that ended because its client disconnected the same way as one that was
released, so `status()` says `"released"` for both. `profile` is a context id,
sent with `persist_profile` as `browserSettings.context`. Its live view lets
whoever opens it drive the session and takes a second request, so
`live_view_url` stays `None` and `control_url()` makes that request when first
called; `expires_at` is the `expiresAt` it reports.

**Hyperbrowser.** Its `cdp_url` is the `wsEndpoint` it reports, which carries a
session token. It takes its lifetime in whole minutes, so `timeout` is rounded
up, and it has no idle timeout, so `idle_timeout` raises. A session ends when
its CDP client disconnects and when every page in it is closed; Hyperbrowser
keeps one alive across a disconnect only with `keepAlive=true` on the connect
URL, which web-router does not add. Its `liveUrl` lets whoever opens it drive
the session, so it is `control_url()`, never `live_view_url`. `profile` and
`persist_profile` map onto `profile.id` and `profile.persistChanges`.
`stealth="off"` sends `useStealth: false` and `useUltraStealth: false`,
`"low"` sends `useStealth: true`, `"high"` sends `useUltraStealth: true`
(enterprise-only on Hyperbrowser's own plans). `solve_captchas` maps onto
`solveCaptchas`, sent as `false` too when asked for. `proxy_country` turns
`useProxy` on and sends `proxyCountry` upper-cased, refused outside the
country list Hyperbrowser's API reference publishes. All three are mapped from
documentation, unverified against a live call.

**Anchor.** Both lifetimes are whole minutes, rounded up. Its `idle_timeout`
starts counting only once the last connection closes, so a client that stays
connected but idle keeps the session, and its bill, alive until `timeout`.
`stealth`, `solve_captchas` and `proxy_country` are mapped from documentation
alone — **unverified against a live call**. Anchor has one stealth tier, so `"low"` and `"high"` both turn on
`browser.extra_stealth.active`, and `"off"` turns it off explicitly. Anchor
silently disables extra stealth unless its proxy is active, so asking for
`stealth="low"` or `"high"` without `proxy_country` raises before a session
opens rather than opening one with less stealth than asked for.
`proxy_country` is sent lower-case, as Anchor's guide shows it, and is checked
against every country its proxy guide lists. `profile` is a profile's name,
sent with `persist_profile` under `browser.profile`. Its live view is
interactive unless the session was opened read-only, so it is `control_url()`,
never `live_view_url`.

**Kernel.** Its `cdp_url` is the `cdp_ws_url` it returns, which carries a
session token. Kernel has no lifetime limit, only an idle one: `idle_timeout`
maps onto `timeout_seconds`, which counts down only while no CDP, WebDriver or
live-view client is connected, and Kernel deletes the session when it runs
out — after 60 seconds if you leave it unset, so connect soon after opening.
`timeout` raises, and the ten-minute default is not sent. Kernel has one
stealth switch, so `"low"` and `"high"` both send `stealth: true`, and `"off"`
sends `false`; stealth also turns on Kernel's CAPTCHA solver and its stealth
proxy, which is why `solve_captchas` raises rather than claiming a separate
switch. `proxy_country` raises too: a country needs a proxy created first
through Kernel's Proxies API, and an existing one can be passed as
`provider_options={"proxy": {"id": ...}}`. `profile` is a Kernel profile's
id — pass a name as `provider_options={"profile": {"name": ...}}` — and
`persist_profile` sends `profile.save_changes`, refused without a `profile`.
Its live view lets whoever opens it drive the session, so it is
`control_url()`, never `live_view_url`; Kernel offers one only for a session
with `headless=False`. A deleted session, whether released or
idled out, reads as `"released"`: Kernel answers 404 for both and does not say
which. Every mapping was checked live on 2026-10-20.

**`local`** starts a fresh browser process per session, with a temporary
profile and its DevTools port pinned to `127.0.0.1`, and stops it and deletes
the profile on release, when its `timeout` runs out, or when your Python
process exits. It looks for `executable_path` in `provider_config`, then
`CHROME_PATH`, then the usual Chrome and Chromium install locations, then
Playwright's downloaded Chromium, and raises `ConfigurationError` naming
`uvx playwright install chromium` when it finds none. It is headless unless you
pass `headless=False` — the one place an unset option is filled in because the
binary's own default, a window, would open on your screen. It maps `viewport`
through CDP's `Browser.setContentsSize` once the browser is up, which gives a
page of exactly that size; `--window-size` cannot, because it sizes the outer
window and even headless Chrome renders a smaller page inside it (800x600 gives
an 800x513 page). A browser too old to have that command fails the open. Its
`provider_options` are Chrome command-line switches, so
`{"window-size": "800,600"}` sets the outer size if that is what you want --
though not beside `viewport`, which refuses every switch that sizes the window.

## Browse

`browse()` hands a task to a vendor's browser agent, which drives one of the
vendor's own browsers through it and answers minutes later. It is research's
shape on the same job core: `browse()` blocks until the answer is in, and
`submit_browse()` hands back a `BrowseJob` to poll instead.

```python
response = router.browse(
    "find the title of the top story and how many points it has",
    start_url="https://news.ycombinator.com",
)

response.answer  # prose, when the agent answered in prose
response.structured  # an object, only when the backend says it fits a schema you asked for
response.steps  # what the backend reported doing, in order: action, url, detail, screenshot_url
response.outcome  # 'complete', or 'partial' only when the backend said it stopped early
response.provider  # who ran it -- never 'auto'
response.raw  # every untouched payload; kept out of repr and serialization

job = router.submit_browse("...", start_url="https://example.com")
job.result(timeout=60)  # raises BrowseTimeoutError and leaves the task going
```

Waiting defaults to `BROWSE_TIMEOUT`, fifteen minutes. Running out raises
`BrowseTimeoutError` carrying the job id, and leaves the task going rather than
cancelling it; `job.cancel()` stops one where the backend can. `raw` stays out
of `repr` and serialization because a task payload can carry the URL of the
session the agent ran in, and live-view, session and recording-control URLs
are never promoted to plain fields.

A backend that cannot start without a page raises `MissingParameterError` when
`start_url` is unset, before anything is billed. One with no structured output
refuses `output_schema` rather than handing back prose under the name of a
schema. [`examples/browse.py`](examples/browse.py) runs a task both ways.

### Browse backends

Every task bills, so there is no keyless tier. Each backend talks to its REST
API over httpx, which `[all]` installs.

| Pass | Name | Provider | Credentials | Options mapped | Verified how |
| --- | --- | --- | --- | --- | --- |
| `Provider.AUTO` *(default)* | `auto` | — | Browser Use, Skyvern or Browserbase, whichever you hold | whatever the member it routes to maps | Routes around a member before any task starts; with no member left it raises, naming why each was skipped |
| `Provider.BROWSER_USE` | `browser-use` | [Browser Use Cloud API v2](https://docs.browser-use.com/cloud/api-v2-overview) | `BROWSER_USE_API_KEY` | `start_url`, `output_schema`, `max_steps`, `allowed_domains` | **Live**, 2026-10-21: a prose task, and each mapped option |
| `Provider.SKYVERN` | `skyvern` | [Skyvern run-task API](https://skyvern.com/docs/sdk-reference/tasks/run-task) | `SKYVERN_API_KEY` | `start_url` (optional) | **Documentation only** — no key was available. `output_schema`, `max_steps` and `allowed_domains` are refused; most answers are only in `raw` (see below) |
| `Provider.BROWSERBASE` | `browserbase` | [Browserbase Agents](https://docs.browserbase.com/platform/agents/overview) | `BROWSERBASE_API_KEY` | none beyond the task | **Live**, 2026-09-30 and 2026-10-21: a prose task. `start_url` is refused; a schema goes through `provider_options` as `resultSchema`, which awaits a live run |

`auto` tries **Browser Use, Skyvern, then Browserbase**, only those you hold a
key for, and nothing after them. Browser Use comes first because it answers in
prose and reports the steps it took; Browserbase last, because its Agents API
has no start URL. A member is skipped, on its class and before any task is
billed, when it does not map an option you set, refuses `start_url`, or needs
one you left out; with none left, `MissingParameterError` or
`UnsupportedParameterError` lists why each was passed over. It falls through to
the next member only on a failure that proves no task was created — a missing
credential, a 4xx, a connection that never reached it — and once a task has
started no second one is. The answer and the job id name the backend that took
the task.

### Browse backend notes

**Browser Use** runs each task in a session it creates for it, and cancelling a
task stops that session too. Its steps carry the page, the screenshot, the
agent's note on the step (its `memory`) and, for a step that took one action,
that action as Browser Use wrote it; the full list of stringified actions, the
step number and the always-empty `nextGoal` are in each step's `extra`.
`output_schema` is sent as Browser Use's `structuredOutput`, and its answer
comes back as `structured`; an answer that is not JSON stays in `answer`
rather than being dropped. `allowed_domains` blocks navigation elsewhere, but
the agent may still answer from what it already knows, so check
`steps` when it matters where an answer came from. A finished task is
`complete` even when Browser Use's self-reported `isSuccess` is `false`, which
stays in `raw`.

**Skyvern** answers in an object whose keys its agent chose unless a
`data_extraction_schema` is sent, and that is not mapped yet, so most Skyvern
answers are only in `raw[0]["output"]`: `answer` is set only when the output is
a string, and `structured` stays empty, because an object nobody asked the
shape of is not structured output. Its recording URL is a presigned link
carrying credentials, so it also stays in `raw`. `timed_out` covers both the
`max_steps` limit and the clock, with nothing to tell them apart, so it fails
the task rather than reading as `partial`. Its `data_extraction_schema`,
`max_steps`, engine and browser session are reachable through
`provider_options` until a live call confirms a mapping.

**Browserbase**'s Agents API has no start URL, so `start_url` raises
`UnsupportedParameterError` rather than being folded into the task. A prose
`result` is the `answer`; an object or list `result` is `structured`, because
Browserbase returns one only for a `resultSchema`, which reaches it through
`provider_options` alongside `agentId`, `variables` and `browserSettings` until
a live call confirms mapping `output_schema`. A run started with no `agentId`
may create a new Agent in your Browserbase account each time. `steps` is empty:
the transcript is a separate endpoint this provider does not read.

## The MCP server

The package ships a **self-hosted stdio MCP server**, so an agent can call these
methods as tools -- `search`, `extract`, `templates` / `template` /
`run_template`, `answer` and `research` -- plus `browse` / `browse_status` for a hosted browser
agent's task, shaped like `research` / `research_status`: the tool waits 25
seconds, then hands back a handle to poll. It runs in your own process and reads your own keys from
your own environment — each key goes only to the backend it belongs to, which is
the whole reason it is self-hosted rather than a service someone runs for you.

Inside the clone, the committed `.mcp.json` already starts it. To add it to
**any** project, or to every project with `--scope user`:

```bash
claude mcp add --scope user web-router -- uv run --project /path/to/webrouter --all-extras web-router-mcp
```

For Claude Desktop, Cursor or another client, the same command as a config block:

```jsonc
{
  "mcpServers": {
    "web-router": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/webrouter", "--all-extras", "web-router-mcp"]
    }
  }
}
```

`--project` keeps the working directory your client started the server in, so
a `.env` in your own project is the one it reads.

That works with **no API key at all**: the keyless `auto` chain gives you search
and extract on first run, and keys are the upgrade path rather than the entry
fee. **Every key you add unlocks more**: the more backends you hold keys for,
the more methods, backends and fallbacks the tools can use. All of them gives
you every capability.

### Giving an installed server your keys

Started from another project, the server runs in whatever directory your client
picks, so it looks for a `.env` in this order and loads the first one that sets
a provider key:

1. the file `WEB_ROUTER_ENV_FILE` names;
2. `.env` in its working directory — the repo root, when this repo's `.mcp.json`
   starts it;
3. the file a `load_env` call with `remember: true` recorded;
4. `.env` in your user config directory: `~/.config/web-router/.env` on Linux
   (under `$XDG_CONFIG_HOME` when that is set), `~/Library/Application Support/web-router/.env`
   on macOS, `%APPDATA%\web-router\.env` on Windows.

So either point the server at a `.env` you already have:

```bash
claude mcp add --scope user web-router -e WEB_ROUTER_ENV_FILE="$HOME/work/.env" -- uv run --project /path/to/webrouter --all-extras web-router-mcp
```

— for Claude Desktop, the same variable goes in the config block's `"env"`:
`"env": {"WEB_ROUTER_ENV_FILE": "/Users/you/work/.env"}` — or put the `.env` in
your user config directory and register the server with no variable at all.
Only provider credentials are read from any of these files, a variable your
client already exports keeps its value, and with no file anywhere the server
runs keyless.

Keys can also arrive mid-conversation. The `load_env` tool takes the **path** of
a `.env` file — the model asks you for it and never for a key — loads its
provider credentials without a restart, and with `remember: true` has the
server read that file on every start. The `web-router://capabilities` resource
says where the server looked, which file it used and which variables it loaded,
by name only; with no key at all it says how to add one.

See [docs/mcp.md](docs/mcp.md) for the tools, their latency budgets, and how
results are shaped for a context window. A browse output never carries the
session the agent ran in: shaped results, `fetch_raw` on a browse handle and
browse errors replace with `[redacted]` every value under a credential-named
field (`token`, `apiKey`, `signingKey`, `password`, `cookie`, `authorization`
and the like), every URL under a live-view, debugger, CDP, websocket or connect
field, and any `ws(s)://` URL, URL with a username, URL with `devtools`,
`cdp`, `live` or `debug` in its host or path, or URL carrying a `token`,
`apiKey`, `jwt`, `key`, `signature` or similar query parameter. See
[docs/mcp.md](docs/mcp.md#a-browse-result-never-carries-its-session) for the
full list.

The `browser_*` tools, installed with `web-router[mcp,browser]`, let the model
drive a browser itself: `browser_open` returns a `session_id`, and
`browser_goto`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_press`, `browser_screenshot` (returned as an image) and
`browser_read` act through it until `browser_close`. The **server holds the
session and the model holds only that id** — one the server minted, never the
backend's — so the CDP URL, the backend's session id and any live-view URL
never enter the transcript. With no key at all, `local` runs a headless browser
on your machine. The server releases a session left unused for five minutes,
refuses a fourth concurrent one, and releases every session when it exits,
whether the client hangs up or it is sent SIGTERM; every session is opened
with a lifetime of at most ten minutes, and SIGKILL can still orphan a `local`
Chrome. `browser_open` does not accept `provider_options` or saved profiles, so
a conversation cannot set Chrome switches. Like every tool's options, `stealth`,
`proxy_country` and `solve_captchas` are offered only when a configured backend
maps them — `stealth` only at the levels one serves — so a keyless install
offers none of them; where they are offered, asking for one with no `provider`
routes past `local` to a billed vendor. The browser opens only `http(s)` pages:
`file:` (unless the operator sets `WEB_ROUTER_MCP_ALLOW_FILE_URLS=1`), browser-internal schemes, cloud
metadata addresses and the session's own debugging endpoint are refused, and a
page that lands on one is sent back to `about:blank`. See
[docs/mcp.md](docs/mcp.md#driving-a-browser).

## Integrating a framework

Which backend serves a call stays the caller's choice. The shipped integrations
are the [MCP server](#the-mcp-server), [LangChain](#langchain),
[Claude](#claude-agents), and [OpenAI](#openai-agents).

`auto` uses every key held; see the [`Provider.AUTO`](#search) row in the search table.

### Framework checklist

When integrating web-router:

- [ ] Accept a `search_provider` or `extract_provider` parameter
- [ ] Default to `"auto"` (web-router's default provider)
- [ ] Pass it to `WebRouter(search_provider)` or `AsyncWebRouter(...)`
- [ ] Use `router.search()` or `router.extract()` in your implementation
- [ ] Let `WebRouterError` propagate or wrap it in your own exception
- [ ] Document credential environment variables (`NIMBLE_API_KEY`, etc.)
- [ ] Document the `auto` behavior above

Error types are in [Errors](#errors). Adding a backend, or keeping one private,
is [Adding a provider](CONTRIBUTING.md#adding-a-provider).

## LangChain

`web_router.integrations.langchain` puts search and extract in front of a
LangChain agent as two tools, `web_search` and `web_extract`:

```python
from langchain.agents import create_agent
from web_router import WebRouter
from web_router.integrations.langchain import web_router_tools

tools = web_router_tools(WebRouter(), provider="nimble", max_results=5)
agent = create_agent("openai:gpt-4.1", tools=tools)
```

Or build them one at a time — `WebRouterSearch(router=..., provider=...,
max_results=..., search_depth=...)` and `WebRouterExtract(...)` — and pass
`async_router=AsyncWebRouter()` to serve `agent.ainvoke` without a worker thread.
`WebRouterAnswer(router=..., provider="linkup")` adds `web_answer`, a backend's
written answer to one question with its sources. It is not among
`web_router_tools()`: every answer backend needs a key and bills each call.

**The model supplies the query or the URLs; you supply everything else.** The
provider, `max_results`, `search_depth` and the domain filters are fixed when
the tool is built and sent unchanged on every call, so an agent behaves the
same whichever backend serves it and switching backends is one argument. An
option the provider does not map raises on the first call, to you — not to the
model, which could not fix it.

**The model reads a compact rendering; you keep the whole response.** Each
tool answers in LangChain's `content_and_artifact` form: the model sees JSON in
one layout for every backend, each result's text cut to `result_chars` (a
page to `document_chars`), and the tool message's `artifact` carries the
untouched `SearchResponse` or `ExtractResponse`. A backend failure reaches the
model as a one-line error it can react to; `web_extract` names the URLs it
could not read rather than failing the call.

## Claude agents

`web_router.integrations.anthropic` gives a Claude agent the same two tools,
as plain custom tool definitions, and the guide that says when to use which:

```python
from anthropic import AsyncAnthropic
from web_router import AsyncWebRouter
from web_router.integrations.anthropic import WebRouterTools

tools = WebRouterTools(AsyncWebRouter(), provider="nimble", max_results=5)
agent = await AsyncAnthropic().beta.agents.create(
    name="researcher",
    model="claude-opus-5",
    system=tools.guide(),  # when to reach for which tool
    tools=tools.definitions(),  # web_router_search, web_router_extract
)

# In the session, for each agent.custom_tool_use event:
result = await tools.acall(event.name, event.input)
await client.beta.sessions.events.send(
    session.id, events=[{"type": "user.custom_tool_result", "custom_tool_use_id": event.id, **result.event_fields()}]
)
```

The definitions serve a Managed Agents agent and the Messages API's `tools`
alike, and the module needs no SDK and no extra: your Anthropic client sends
them, and `call` / `acall` run each call here, on your keys. The options rule
is the LangChain tools' — the model supplies the query or the URLs, you fix the
rest — and so is the rendering, byte for byte. Input the model got wrong comes
back as an error result it can fix; a backend failure does too; your own setup
mistake raises. The tools are named `web_router_search` and
`web_router_extract`, never `web_search`, so an agent that also holds Claude's
built-in search cannot confuse the two.

**Answer is opt-in.** `methods=["search", "extract", "answer"]` adds
`web_router_answer`: one question in, the backend's written answer and its
citations out, each snippet cut to `result_chars`. It goes to `answer_provider`,
else to `provider` when that backend answers, else to the router's default
answer backend, and the tools raise `NoAnswerProviderError` when they are built
if there is none to use. It sends only the question; the search options are not
passed to it.

`routing_guide()` (or `tools.guide()`, for exactly the tools you expose) is a
first version of the judgment half: cheapest tool first, one fact per search,
read a page only when its snippet falls short. Research and browse join the
tools once the guide can say when one is worth its bill.

## OpenAI agents

`web_router.integrations.openai` gives an OpenAI model the same two tools, as
function tool definitions:

```python
from openai import OpenAI
from web_router import WebRouter
from web_router.integrations.openai import WebRouterTools

client = OpenAI()
tools = WebRouterTools(WebRouter(), provider="nimble", max_results=5)
request = {"model": "gpt-4.1", "instructions": tools.guide(), "tools": tools.definitions()}

response = client.responses.create(input="Who won the 2026 Tour de France?", **request)
while calls := [item for item in response.output if item.type == "function_call"]:
    outputs = [tools.call(c.name, c.arguments).function_call_output(c.call_id) for c in calls]
    response = client.responses.create(input=outputs, previous_response_id=response.id, **request)
print(response.output_text)
```

It is the Claude module with an OpenAI envelope: the same constructor, tools,
rendering, errors and routing guide, and no SDK or extra. `definitions()` is
the Responses API shape and `chat_definitions()` the Chat Completions one;
answer a call with `function_call_output(call_id)` or
`tool_message(tool_call_id)`. `call` / `acall` take the arguments as the JSON
string OpenAI hands over, or already decoded, and arguments that are not valid
JSON come back as an error result the model can fix. The definitions are
strict, so OpenAI holds the model's arguments to the schema. OpenAI has no
error flag on a tool's output, so an error's text says on its own that the
call failed or did not run. The tools keep their `web_router_` names, so they
never collide with OpenAI's built-in `web_search` either.

## Wrapping `search` for your own agent

The integrations above already fix `max_results` when the tool is built, so the
model never picks it. A model that may pick it will sometimes ask for one
result and answer thinly. For a framework with no integration, do the same:
keep `max_results` out of the schema the model sees and pin 5 to 10 yourself.
`WebRouterTools` gives you that schema and the compact rendering, whatever
sends them:

```python
from web_router import WebRouter
from web_router.integrations.anthropic import WebRouterTools

tools = WebRouterTools(WebRouter(), methods=["search"], max_results=8)
schema = tools.definitions()[0]["input_schema"]  # only `query`


def web_search(query: str) -> str:
    return tools.call("web_router_search", {"query": query}).content
```

If you do expose `max_results`, its description in `SearchRequest`'s JSON
schema tells the model when to ask for more. The MCP server shows it the same
text.

## Agent benchmark

[`benchmarks/agent/`](benchmarks/agent/README.md) answers the question the
router has to win: the same Claude agent on its **built-in web search**, against
the same agent on **web-router's tools and routing guide**. Each arm is a
Managed Agents agent with the same model and task prompt, run over
[FRAMES](#evaluating-accuracy); a `:no-guide` arm keeps the tools and drops the
guide, so the tools' share and the guide's share are measured apart. The report
puts judged accuracy beside tool calls, tokens, Claude's web searches, the list
cost Anthropic reports and latency.

## Benchmarking providers

The package promises that a call behaves the same whichever backend serves it
— not that every backend serves it equally well. `benchmark()` runs one search
through several providers at once, each through the same `search()` path you
would call yourself, and reports what each did on *your* query with *your*
keys:

```python
report = router.benchmark("nvidia q3 earnings", providers=["nimble", "tavily", "you-mcp"], max_results=5, timeout=20)

for run in report.runs:
    if run.ok:
        print(f"{run.provider:10} {run.latency * 1000:6.0f} ms  {run.count} results")
    else:
        print(f"{run.provider:10} {run.error}")

report["tavily"].response.urls  # the full SearchResponse rides along
```

```python
report = await AsyncWebRouter().benchmark("nvidia q3 earnings", timeout=20)
```

Leave `providers` unset to compare every search backend this environment can
use — the ones you hold keys for plus the keyless tier — minus `auto`, which
would only repeat one of them. The other search options (`search_depth`,
`country`, …) go to every row unchanged.

What the report carries is **measured facts only**: wall-clock latency around
the call, the result count, and the full `SearchResponse` so you can apply your
own judgment. There is no quality score, relevance estimate or price — judging
results against your task is your (or your LLM judge's) job, and any price
table shipped here would drift; when you want that judgment made for you, on a
question set with known answers, that is [`evaluate()`](#evaluating-accuracy).
What it never does is fail as a whole: a
provider that refuses an option, has no key, errors, or runs out of `timeout`
is its own row's `error` (a `BenchmarkTimeoutError` for the last), and the
others are unaffected.

`benchmark_extract()` is the same comparison over `extract()`: the same URLs
through several extract backends at once, each row measured around the same
`extract()` call you would make, with the documents that came back and the
URLs that did not:

```python
report = router.benchmark_extract(["https://example.com", "https://arxiv.org/abs/1706.03762"], timeout=60)

for run in report.runs:
    if run.ok:
        print(f"{run.provider:10} {run.latency * 1000:6.0f} ms  {run.count} pages, {len(run.response.failures)} failed")
    else:
        print(f"{run.provider:10} {run.error}")

report["nimble"].response.documents[0].content  # the full ExtractResponse rides along
```

Leave `providers` unset to compare every extract backend this environment holds
credentials for plus the keyless Jina Reader, minus `auto`. `format` goes to
every row unchanged; leave it unset and each backend answers in markdown where
it can and its own default where it cannot, so every backend serves — name one
and a backend that cannot return it is that row's `UnsupportedFormatError`,
never a substitution. A call that fetched some of the URLs and not others is an
answered row whose `response.failures` say which, exactly as the call itself
stays partly successful; only a call where nothing came back is the row's
error. The same measured facts only: how long, what came back, what did not.
Whether a page's text is complete or correct is your judgment, as relevance is
for a search. [`benchmarks/providers/extract.py`](benchmarks/providers/README.md#comparing-extract-backends)
runs it over a URL set and tabulates the rows per backend and per category.

`search` and `extract` only: `research`, `browse` and `open_browser` bill per
run or per minute, and starting several at once to compare them is a decision
to make explicitly per provider, not a default a comparison method should hide.

## Evaluating accuracy

`benchmark()` tells you how fast each backend answered and how much came back.
`evaluate()` tells you how *right* it was: it runs a set of questions with
known answers through several providers, has an LLM judge turn each provider's
results into one answer and grade it against the gold, and scores every
provider. One fixed judge grades every backend, so the only thing that varies
between rows is the retrieval feeding it:

1. Each question is one `benchmark()` fan-out — the same `search()` call you
   would make, one row per provider.
2. For every provider that answered, the judge synthesizes one answer from its
   ranked results (system prompt: *"Answer the query using only the provided
   search results."*) with one fixed model, so the only thing that differs
   between providers is the retrieval feeding it.
3. The judge grades that answer against the gold with the SimpleQA grader
   prompt from [`openai/simple-evals`](https://github.com/openai/simple-evals),
   vendored byte-for-byte: `correct`, `incorrect` or `not_attempted`. It is
   MIT-licensed, © 2024 OpenAI, and ships with its license beside it at
   `web_router/evals/prompts/LICENSE-simple-evals`.

The built-in judge is `OpenAIJudge`, which uses `gpt-4o`, and needs the `evals`
extra and `OPENAI_API_KEY`:

```bash
make setup   # the evals extra is part of every extra
export OPENAI_API_KEY=...   # the judge only; no search backend reads it
```

```python
from web_router import WebRouter
from web_router.evals import Dataset

router = WebRouter()
dataset = Dataset.simpleqa(limit=50)  # the canonical seeded slice of the SimpleQA test set
report = router.evaluate(dataset, providers=["nimble", "tavily", "brave"])

for score in report.scores:
    print(
        f"{score.provider:10} {score.accuracy or 0:6.1%}  "
        f"failure rate {score.failure_rate:.1%}  p50 {(score.latency_p50 or 0) * 1000:5.0f} ms"
    )
```

`AnthropicJudge` is the other built-in, on `claude-sonnet-5-5` with
`ANTHROPIC_API_KEY` and the same extra — for when what you are comparing is
built on GPT and `gpt-4o` would be grading its own family:
`router.evaluate(dataset, judge=AnthropicJudge())`. Its report reads
`anthropic:claude-sonnet-5-5`, and its figures compare only with that judge's.

`Dataset.simpleqa()` downloads the test set from `openai/simple-evals` (pass
`path=` for a local copy); `limit` samples with `random.Random(seed)` exactly as
`simple-evals` does, so `limit=500` with the default seed is the same 500
questions every published SimpleQA figure was measured on. `Dataset.frames()`
is Google's [FRAMES](https://huggingface.co/datasets/google/frames-benchmark)
test set, sampled the same way: 824 multi-hop questions, each put together from
several Wikipedia articles, so one search rarely settles it — a test for an
agent more than for one retrieval. `Dataset.simpleqa_verified()` is Google
DeepMind's [SimpleQA Verified](https://huggingface.co/datasets/google/simpleqa-verified),
1,000 cleaned single-fact questions pinned to one commit of the dataset.
`Dataset.sealqa("seal-0" | "seal-hard")` is [SealQA](https://huggingface.co/datasets/vtllms/sealqa)
(111 and 254 questions, pinned), built so that search turns up conflicting or unhelpful
pages; it needs `pyarrow`, which the `evals` extra installs, and it withholds results from
its own pages and copies before judging, so a backend is not scored on finding the answer
key (each row's `withheld` counts them). `Dataset.for_set(name)` loads any of
these by the name the benchmarks use (`simpleqa`, `simpleqa-verified`, `frames`,
`sealqa-0`, `sealqa-hard`), or `csv` with a `path`. Your own questions
come from a CSV with a header, and any `Judge` subclass can stand in for the
built-in one — another vendor, a local model:

```python
from web_router.evals import Dataset, Judge


class MyJudge(Judge):
    name = "mine"

    def _complete(self, system: str, user: str) -> str:
        return my_model.chat(system, user)  # one prompt in, the model's text out

    async def _acomplete(self, system: str, user: str) -> str:
        return await my_model.achat(system, user)


dataset = Dataset.from_csv("questions.csv", question_column="question", answer_column="expected")
report = router.evaluate(dataset, judge=MyJudge())
```

`evaluate()` calls the judge's `check()` before the first search, so override
it to raise on a setup that cannot work — the built-in judges raise there on a
missing SDK or key.

Two things about the knobs. A `Dataset` is a model, so loop over
`dataset.questions`, not the dataset itself. And `search_depth` and
`max_results` go to every provider in the call unchanged, which is what makes
the rows comparable — so to pin each backend to a particular vendor tier, run
`evaluate()` once per depth on the same dataset and set the tables side by
side. The [search-depth table](#search-depth) says what each vendor makes of
`lite` / `fast` / `deep`; for example Nimble's `lite` and Tavily's `basic` are
both `search_depth="lite"`, while You.com's `highlights` extraction is
`search_depth="fast"`.

Each `ProviderScore` carries `questions`, `served`, `failed`, `correct`,
`incorrect`, `not_attempted`, `failure_rate` and `latency_p50` / `latency_p95`
(search latency alone; judging is not counted), and two accuracies. `accuracy`
is `correct / served`: a question the backend served but the answer did not
attempt counts as a miss, because the call reached the backend and failing to
answer costs what answering wrong does. `accuracy_when_attempted` is
`correct / (correct + incorrect)`, the figure `simple-evals` publishes, which
leaves `not_attempted` out. A row that failed — at the search, or at either
judge call — is excluded from both and counted in `failure_rate`, with the
error and the stage under `EvaluationRow.error` / `failed_at`. Every row is in
`report.rows`, answer and grade included, so a surprising score can be read
back to the question that produced it.

Three things to keep in mind. **Accuracy is a judgment, not a measurement**:
the report names the judge (`report.judge`, e.g. `openai:gpt-4o`), and the
number is comparable only with figures the same judge produced. **It bills**:
every question is one search per provider plus two judge calls per provider
that answered, so 500 questions across three providers is 1,500 searches and
up to 3,000 LLM calls — start with `limit=20`. The judge calls dominate the
cost: the grader prompt is ~1,600 tokens every time, and the synthesis prompt
carries the results' text, from ~1,000 tokens for a snippet-only backend to
~3,500 for one that returns long excerpts at ten results. And it **fails fast**: a
missing key or SDK for a built-in judge raises before the first search is
paid for. `evaluate()` is `search` only, for the same reason `benchmark()` is.

## Routing and extending

### Routing between providers

```python
router = WebRouter(
    Provider.NIMBLE,
    provider_config={
        Provider.NIMBLE: {"api_key": "..."},
        Provider.TAVILY: {"api_key": "...", "timeout": 30},
    },
)

response = router.search("query", provider=Provider.TAVILY)  # per-call override
```

`provider_config` is the one place credentials and client settings are passed
explicitly; everything else comes from the environment. It reaches a backend
`auto` calls as well as one you name, and a backend given an `api_key` there
joins `auto`'s default chain as one whose key is in the environment does. Providers are
constructed lazily and cached, so configuring a backend you never call costs
nothing.

Switching backend on every call? Build the provider once and pass it, so its
settings travel with it instead of being restated:

```python
from web_router.providers.tavily import TavilyProvider

tavily = TavilyProvider(api_key="...", timeout=30)

for query in queries:
    router.search(query, provider=tavily)
```

A provider you construct is yours: the router uses it, never caches it, and
`router.close()` leaves it open for you to close.

Already hold an authenticated Nimble client — one per end user, say, with
your own headers and a connection pool shared across your app? Pass it as
`async_client` to any of the three Nimble classes, directly or through
`provider_config`, and the provider calls through it instead of building one
from a key:

```python
from nimble_python import AsyncNimble

client = AsyncNimble(api_key=user_key, default_headers={...})

async with AsyncWebRouter(provider_config={Provider.NIMBLE: {"async_client": client}}) as router:
    await router.search("query")  # auto counts Nimble as configured, no NIMBLE_API_KEY needed
```

The client stays yours: the provider reads no key, builds no sync client — a
sync call raises `ConfigurationError`, and so does a synchronous `WebRouter`
handed one in `provider_config`, rather than letting `auto` pass over Nimble
unannounced — and never closes it. Its credential is the one used: an
`api_key` passed beside it raises `ConfigurationError` instead of replacing it.
Other options passed beside it, such as `timeout`, apply to a copy that shares
its connection pool, and research keeps the client's own timeouts, clamping
only the read limit to the run's budget.

### Estimated prices (experimental)

`web_router.prices` is the price list the library ships: the published list
price of every (provider, method, mode) web-router calls, each with the vendor
page it comes from and the date it was checked. It is experimental and may
change before 1.0. The figures are estimates -- pay-as-you-go list prices, not
your plan's or a negotiated rate, and a call can bill more than its row (content
add-ons, tokens) -- and the router never puts one in a response.

```python
from web_router import prices

prices.estimate("tavily", "search")  # 0.008: one request at the default depth
prices.estimate("tavily", "search", "fast")  # 0.016: search_depth="fast"
prices.estimate("linkup", "research", "max")  # 2.5: one run at effort="max"
prices.estimate("perplexity", "research")  # None: billed by tokens
row = prices.price("exa", "search")  # the whole row: unit, note, source_url, checked
```

`estimate()` is the cost of one request, page, run or browser-minute, and
`None` when the vendor publishes no price (`unit="unknown"`), bills by tokens
(`unit="token_billed"`), or the list has no row for that mode -- never another
mode's price. A mode is web-router's own `search_depth` or `effort` value, or a
vendor mode sent through `provider_options` written with a leading `:`
(`":ultra-fast"`); leave it unset for a call that sets none.
[`PRICING.md`](benchmarks/providers/PRICING.md) shows the same list as tables,
with the notes behind each figure.

A search or answer row also carries measured latency, from the committed
benchmark results in `benchmarks/providers/frozen/`: the median and
90th-percentile seconds of the calls that succeeded, how many there were
(`latency_n`), the runs pooled (`latency_runs`) and the newest one's date
(`latency_measured`).

```python
prices.estimate_latency("exa", "answer")  # (1.317, 1.618): p50 and p90 seconds
prices.estimate_latency("tavily", "extract")  # None: not measured
```

It is `None` for every method and mode no benchmark measured -- extract,
research, browse and the modes the scans did not run -- never a guess. A
benchmark latency drifts from run to run, which is why each row pools every
run that covers it. `make latency` rewrites the fields from the frozen
results, and a test fails while they disagree.

### Classifying a query before running it (spike)

`classify()` is an opt-in spike, off unless you pass `decider=`. It asks a
decision model a few short questions about the query and turns the answers
into a `Route`: the method (search, extract, research or browse) and the
`auto` chain to run it on. It runs nothing itself; `execute()` runs the route,
or you read it and call a method yourself. It is a baseline for measuring
decision models, not a stable routing contract, and may change.

```python
router = WebRouter(decider="keywords")  # keyless

route = router.classify("Summarize https://example.com/pricing")
route.method  # "extract"
route.fallback  # () -- or why the route is the default one
print(route)  # the labels as plain instructions, for an agent to read
response = router.execute(route)
```

The deciders:

- `keywords`: simple keyword rules, keyless and offline. Its confidences are
  fixed, not measured; a research or browse pick is below the threshold, so
  it falls back to search.
- `jev`: TypeSafe's Jev, reading `TYPESAFE_API_KEY`.
- `llm`: a chat model with structured outputs over any OpenAI-compatible API,
  with the `evals` extra. Configure it with `decider_config`, as
  `provider_config` configures a backend:
  `WebRouter(decider="llm", decider_config={"model": "...", "base_url": "...", "api_key": "..."})`;
  without an `api_key` it reads `OPENAI_API_KEY`.
- A name given to `register_decider()`, or any object with `decide()` and `close()`.

Every decider reports confidence on one scale, how far its pick stands above
an even guess, and a label below 0.5 takes its default. Passing `urls` routes
to extract and `output_schema` to research without asking the decider. A
decider that fails or raises, or a label it is unsure of, gives today's
default, search on the default chain, and `route.fallback` says why:
`decider_error`, `low_confidence`, `no_url` or `no_backend_for_method`.

The query text is sent to the decider you choose, so `jev` and `llm` send it
to that service.

### Which providers can I actually use?

```python
from web_router import available_providers, configured_providers

available_providers()  # every name the router knows
configured_providers()  # the ones you have a credential for, plus the keyless ones
```

Both return plain strings, not `Provider` members: they report what is
registered at runtime, which includes backends you added yourself and so cannot
be an enum. Feed a result straight back to `search(provider=...)`.

`configured_providers()` reads environment variables only — a key passed through
`provider_config` will not show up there (a router's `auto` still counts it), and a key that is present may still be
rejected by the service. It answers "worth trying", not "guaranteed to work".

### Reaching a provider's own options

Every search API has capabilities beyond the shared surface. Those pass through
untranslated:

```python
router.search("nvidia earnings", provider_options={"topic": "news"})
```

`provider_options` is an escape hatch, not a destination. Options used through
it are provider-specific by definition, so the portability guarantee above does
not apply to them.

**If you work on a search API, this is the part to change.** Promoting one of
your options into the shared surface — so it works the same way across every
backend — is a small, well-defined PR, and the fastest way to make web-router
represent your API properly. See
[CONTRIBUTING.md](CONTRIBUTING.md#promoting-a-provider-option).

### Adding a provider

Most of these APIs have no Python SDK worth the dependency, so the short path is
`HttpSearchProvider` — it owns the client, auth headers, HTTP error handling and
cleanup, and leaves you two methods:

```python
from web_router import register_provider
from web_router.providers import HttpSearchProvider, build_payload
from web_router.types import SearchRequest, SearchResponse, SearchResult


class AcmeProvider(HttpSearchProvider):
    name = "acme"
    supported_parameters = frozenset({"max_results"})
    base_url = "https://api.acme.com"
    search_path = "/v1/search"
    env_api_key = "ACME_API_KEY"

    def _build_payload(self, request: SearchRequest) -> dict:
        return build_payload(request, {"max_results": "limit"}, query=request.query)

    def _to_response(self, request: SearchRequest, payload: dict) -> SearchResponse:
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[SearchResult(url=hit["url"], title=hit["title"]) for hit in payload["hits"]],
            raw=payload,  # nothing is thrown away
        )


register_provider("acme", AcmeProvider)  # custom names are strings: the enum is closed
```

Wrapping a real SDK instead? Subclass `SearchProvider` and implement `_search`
and `_asearch`, as `nimble.py` and `tavily.py` do.

Either way you write translation only. The base class rejects options you did
not declare and wraps whatever your backend raises in `ProviderError` — which is
why you implement `_search` and callers use `search`. `build_payload` handles the
other two: it drops the options you left unset and always passes
`provider_options` through last.

An extract backend is the same shape against `HttpExtractProvider`, with
`extract_path` in place of `search_path` and a `format` table instead of a depth
one:

```python
from web_router import register_extract_provider
from web_router.providers import HttpExtractProvider, build_payload
from web_router.types import ExtractedDocument, ExtractRequest, ExtractResponse

ACME_FORMATS = {"markdown": "md", "html": "raw"}


class AcmeExtractProvider(HttpExtractProvider):
    name = "acme"
    supported_parameters = frozenset({"format"})
    supported_formats = frozenset(ACME_FORMATS)  # the table is the single description
    default_format = "html"
    base_url = "https://api.acme.com"
    extract_path = "/v1/fetch"
    env_api_key = "ACME_API_KEY"

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict:
        return build_payload(request, url=urls[0], as_=ACME_FORMATS[self.format_of(request)])

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict) -> ExtractResponse:
        return ExtractResponse(
            provider=self.name,
            documents=[
                ExtractedDocument(
                    # This call's own URL, not `request.urls[0]` -- the base
                    # class calls you once per URL, so the request holds them all.
                    url=urls[0],
                    content=payload["body"],
                    format=self.format_of(request),
                )
            ],
            raw=[payload],
        )


register_extract_provider("acme", AcmeExtractProvider)
```

`batches_urls` is left alone above, so the base class calls this once per URL and
assembles one response. Set it to `True` only if a single call really takes the
whole list, as Tavily's and Exa's do.

`perplexity.py` and `firecrawl.py` are the shortest complete examples, and
[CONTRIBUTING.md](CONTRIBUTING.md#adding-a-provider) has the full checklist —
keyless endpoints, redirects, and reusing an existing provider's normalization.

## Errors

Every exception the router raises derives from `WebRouterError`, so one `except`
covers the lot:

```python
from web_router import Provider, WebRouter, WebRouterError

try:
    WebRouter(Provider.TAVILY).search("...")
except WebRouterError as exc:
    print(exc)
```

| Exception | Raised when | Typical fix |
| --- | --- | --- |
| `ConfigurationError` | No API key for a provider you named | Set the variable it names, or pass `provider_config` |
| `ProviderNotInstalledError` | The extra carrying the provider's SDK is not installed | `make setup` in the clone installs every extra; the message names the one missing |
| `ProviderNotFoundError` | Unknown provider name, or one that does not serve the method you called | The message lists every name that method does serve |
| `UnsupportedParameterError` | The provider does not map an option you set | Use `provider_options`, or add the mapping |
| `UnsupportedFormatError` | The extract or template backend cannot return the `format` you asked for | Ask for one it can — the message lists them — or name another backend |
| `TemplateNotFoundError` | A template run or describe named a template the vendor does not have | A `ProviderError`; list the vendor's templates with `templates()` — names are the vendor's own |
| `MissingParameterError` | The browse backend cannot start without an option you left unset — `start_url`, today | Set it, or name a backend that finds its own first page; nothing was billed |
| `ResearchTimeoutError` / `BrowseTimeoutError` | A research run or browse task outlasted the timeout you allowed it | Nothing is lost — the run is still going and the exception carries its `job_id`. Poll it, or allow longer |
| `BenchmarkTimeoutError` | Never raised: it is the `error` of a `benchmark()` or `benchmark_extract()` row whose provider had not answered within the comparison's `timeout` | Allow longer, or leave that provider out of `providers` — the other rows are unaffected |
| `ProviderError` | The backend itself failed — HTTP error, SDK exception, bad credentials | Depends on the backend; the original error is wrapped, not swallowed |
| `BrowserActionError` | A page action on a browser handle failed; subclasses say how — see [Driving the page](#driving-the-page) | A `StaleElementError` wants a fresh `snapshot()`; the rest carry Playwright's error on `__cause__` |

These are **siblings, not a hierarchy**: `ProviderNotInstalledError` is not a
subclass of `ConfigurationError`, so `except ConfigurationError` will not catch
a missing extra. Catch `WebRouterError` unless you mean one specific case.

## Configuration

Keys are read from the environment, or passed explicitly through
`provider_config={Provider.NIMBLE: {"api_key": "..."}}`:

| Provider | Variable | Serves |
| --- | --- | --- |
| Nimble | `NIMBLE_API_KEY` | search, extract, research |
| Tavily | `TAVILY_API_KEY` | search, extract |
| Exa | `EXA_API_KEY` | search, extract, answer |
| You.com | `YOU_API_KEY` | search, research, answer |
| Brave | `BRAVE_SEARCH_API_KEY` | search |
| Parallel | `PARALLEL_API_KEY` | search, research, answer |
| Perplexity | `PERPLEXITY_API_KEY` | search, research, answer |
| Linkup | `LINKUP_API_KEY` | search, extract, research, answer |
| WebIQ | `WEBIQ_API_KEY` | search, extract |
| Keenable | `KEENABLE_API_KEY` *(optional for `auto`, which uses its keyless tier without one)* | search, extract |
| DeepSeek | `DEEPSEEK_API_KEY` | search, extract |
| AgentCore Web Search | `AGENTCORE_GATEWAY_URL`, plus an AWS credential from botocore's usual chain *(not a key: see [setup](docs/agentcore-setup.md))* | search |
| Firecrawl | `FIRECRAWL_API_KEY` | extract |
| Oxylabs | `OXYLABS_USERNAME` + `OXYLABS_PASSWORD` | extract |
| Bright Data | `BRIGHTDATA_API_TOKEN` + `BRIGHTDATA_ZONE` | extract |
| Jina Reader | `JINA_API_KEY` *(optional — raises the rate limit)* | extract |
| Steel | `STEEL_API_KEY` | open_browser |
| Browserbase | `BROWSERBASE_API_KEY` | open_browser, browse |
| Hyperbrowser | `HYPERBROWSER_API_KEY` | open_browser |
| Anchor | `ANCHOR_API_KEY` | open_browser |
| Kernel | `KERNEL_API_KEY` | open_browser |
| Browser Use | `BROWSER_USE_API_KEY` | browse |
| Skyvern | `SKYVERN_API_KEY` | browse |
| `local` | *none — keyless* (`CHROME_PATH` optional, the browser to launch) | open_browser |
| `you-mcp` | *none — keyless* | search |
| `exa-mcp` | *none — keyless* | search |
| `parallel-mcp` | *none — keyless* | search |
| `auto` | *none — keyless* | search, extract, open_browser |
| OpenAI *(the default `evaluate()` judge, not a backend)* | `OPENAI_API_KEY` | evaluate |
| Anthropic *(`AnthropicJudge` for `evaluate()`, not a backend)* | `ANTHROPIC_API_KEY` | evaluate |

A key set once serves every method of a provider that has several. See
[`.env.example`](.env.example). The library reads the environment only; the
MCP server is the one place a `.env` is read for you — see
[Giving an installed server your keys](#giving-an-installed-server-your-keys).
`configured_providers()` and
`configured_extract_providers()` — and `configured_research_providers()`,
`configured_browser_providers()` and `configured_browse_providers()` — report
which of these the current environment actually satisfies — a backend needing two variables only when it has both.

web-router does not log or store keys, and a provider's `__repr__` omits them so
a stray log of a provider does not leak one. Provider clients also never follow redirects —
`httpx` would replay the auth header to the redirect target, including one on
another host — so a provider sending a credential rejects
`follow_redirects=True`. See [SECURITY.md](SECURITY.md#credentials).

## Development

```bash
make install   # venv + all extras + dev deps + pre-commit hooks
make check     # lint, typecheck, unit tests
```

`make test` runs offline and needs no credentials. Tests that hit a live API are
marked `integration` and read a gitignored `.env`; any provider whose key is
missing skips rather than fails, so you only need keys for the backends you are
working on:

```bash
cp .env.example .env   # then fill in the keys you have
make test-integration
```

Tests that launch a real Chromium on this machine are marked `browser`, skip
when none is installed, and stay out of `make test`:

```bash
make test-browser
```

- [CONTRIBUTING.md](CONTRIBUTING.md) — setup and the PR workflow
- [STYLE_GUIDE.md](STYLE_GUIDE.md) — the standards code is reviewed against
- [AGENTS.md](AGENTS.md) — conventions for AI coding agents working on this repo
- [docs/mcp.md](docs/mcp.md) — the MCP server, for agents *using* the package
- [docs/agent-prompt.md](docs/agent-prompt.md) — a baseline system prompt for
  an agent whose tools are web-router's, and
  [skills/web-router/SKILL.md](skills/web-router/SKILL.md), the same contract
  packaged as an agent skill

## License

`web-router` is proprietary and shared with alpha users under their agreement
with Nimble; no open-source license applies to this repository. The SimpleQA
grader prompt vendored at `src/web_router/evals/prompts/` keeps its own MIT
license, in `LICENSE-simple-evals` beside it.
