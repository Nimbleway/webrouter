# Provider pricing and modes

A reference for reading benchmark results against cost: what every backend
web-router calls costs at list price, for each method (search, extract,
research, browser sessions and browse tasks), and how a benchmark setup reaches
each search mode. No
code in web-router reads this file, and the library never shows a price, because
a price table shipped in code would drift and state as fact what a vendor did
not report. This one is dated and sourced so it can be checked and redone
instead.

**Checked 2026-10-07** from each vendor's own docs and pricing pages (search
and extract first checked 2026-09-30). Prices are pay-as-you-go list prices in
US dollars per 1,000 requests unless stated; a vendor that publishes no price
is marked "not published" rather than estimated;
volume plans and negotiated rates differ. Recheck a source before quoting a
figure, and update the date when you do.

**How a setup reaches a mode.** `provider@lite|fast|deep` uses web-router's
normalized `search_depth`, where the backend maps it. `provider:mode` sends a
vendor mode from [`modes.json`](modes.json) through `provider_options`. A mode
marked unverified has not yet been shown by a live call to do what its docs
say.

## Search

### Nimble

| Mode | Setup | What it returns | Price per 1k | Source |
|---|---|---|---|---|
| `search_depth=lite` | `nimble@lite` | Titles, URLs, snippets | $1.10 | [docs pricing](https://docs.nimbleway.com/nimble-sdk/admin/pricing) |
| `search_depth=standard` (the default) | `nimble`, `nimble@fast` | ~2K characters per result | **$5.00**; see below | same; [pricing page](https://www.nimbleway.com/pricing) |
| `+ full_content=true` | `nimble@deep` (standard), `nimble:full-content@lite` | The whole page, scraped live | depth price + $1.00 per 1k URLs | docs pricing |

**Standard is $5.00 (checked 2026-10-02).** nimbleway.com/pricing headlines
Search at "$1.1 /1k requests" with "Lite / Standard" beneath it; that is the
lite tier's price. Nimble's docs price the two depths separately, lite $1.10
and standard $5.00, pay-as-you-go. A request that names no depth gets
`standard`: a live search with none set returned 3,000 to 11,000 characters of
page content per result, which lite does not return. Every benchmark run so far
used that default, web-router and Nimble's own MCP tool alike, so $5.00 is
the rate to price them at. `include_answer` was removed from Search in
July 2026, and `focus` modes (news, coding, shopping, …) need `lite`. Plans are
priced in "web pages" (Startup $2,500/mo for 350K, Scale $7,000/mo for 1.2M,
Professional $15,000/mo for 3M; not re-verified 2026-10-07, the page did not
return them), and no page says how many a search uses.

### Tavily

A credit costs $0.008 on pay-as-you-go ([api credits](https://docs.tavily.com/documentation/api-credits)).

| Mode | Setup | What it returns | Credits | Price per 1k | Source |
|---|---|---|---|---|---|
| `search_depth=basic` (the default) | `tavily`, `tavily@lite` | Several snippets per URL | 1 | $8 | [search reference](https://docs.tavily.com/documentation/api-reference/endpoint/search) |
| `search_depth=advanced` | `tavily@fast` | Most relevant snippets, slower | 2 | $16 | same |
| `advanced + include_raw_content` | `tavily@deep` | Adds the page body | 2 | $16 | same |
| `search_depth=fast` | `tavily:fast` (unverified) | Lower latency | 1 | $8 | same |
| `search_depth=ultra-fast` | `tavily:ultra-fast` (unverified) | Lowest latency, one summary per URL | 1 | $8 | same |

`auto_parameters=true` (Tavily picks the settings) costs 2 credits. Plans:
Project 4,000 credits for $30, Bootstrap 15,000 for $100, Startup 38,000 for
$220, Growth 100,000 for $500.

### Exa

| Mode | Setup | What it does | Price per 1k | Source |
|---|---|---|---|---|
| `type=auto` (the default) | `exa` | Balances quality and speed | $7 + contents | [pricing](https://exa.ai/pricing), [search reference](https://exa.ai/docs/reference/search) |
| `type=instant` | `exa:instant` (unverified) | Fastest | $4 + contents | same |
| `type=fast` | `exa:fast` (unverified) | Low latency | $7 + contents | same |
| `type=deep-lite` | `exa:deep-lite` (unverified) | Light research with synthesis, ~4s | $12 + contents | same |
| `type=deep` | `exa:deep` (unverified) | Multi-step research | $12 + contents | same |
| `type=deep-reasoning` | not in modes.json | Stronger reasoning | $15 + contents | same |

Contents are billed per page per content type, $1 per 1k each: text, highlights
or summary. web-router always asks for text on 5–10 results, so a default
search costs about $7 + $1 × results per 1k, **roughly $12–17 per 1k** (derived
from the stated prices, not stated by Exa). `exa:highlights` swaps text for
highlights at the same rate. Results beyond 10 are +$1 per 1k.

### Perplexity (Search API)

| Mode | Setup | What it does | Price per 1k | Source |
|---|---|---|---|---|
| `search_type=web` (the default) | `perplexity` | General web search | $5 | [pricing](https://docs.perplexity.ai/getting-started/pricing), [search reference](https://docs.perplexity.ai/api-reference/search-post) |
| `search_type=fast` | `perplexity:fast` (unverified) | Lower latency, lower cost | $1 | same |
| `search_context_size=low / medium / high` (high is the default) | `perplexity:context-low`, `perplexity:context-medium` (unverified) | Content per page | not stated for the Search API (the page prices context size for Sonar only) | same |

A request with several queries is billed as one request.

### Brave

| Mode | Setup | What it does | Price per 1k | Source |
|---|---|---|---|---|
| Search plan | `brave` | Full web results | $5 ($5 free credit per month) | [Brave Search API](https://brave.com/search/api/) |
| `extra_snippets=true` | `brave:extra-snippets` (unverified) | Up to 5 extra excerpts per result | included | [query docs](https://api-dashboard.search.brave.com/app/documentation/web-search/query) |
| Answers plan | not a search mode | Summarized, cited answers | $4 + $5 per million tokens | Brave Search API |

The LLM Context endpoint (ranked page chunks for an LLM) has no stated price.

### Parallel

| Mode | Setup | What it does | Price per 1k | Source |
|---|---|---|---|---|
| Default on `/v1beta/search` | `parallel` | What web-router calls today | not stated for v1beta | [pricing](https://parallel.ai/pricing) |
| `processor=pro` (v1beta) | `parallel:processor-pro` (unverified) | Legacy tier selector | not stated | [llms-full.txt](https://docs.parallel.ai/llms-full.txt) |
| `mode=turbo` (v1) | not reachable yet | ~200 ms, English and Japanese only | $1 | [modes](https://docs.parallel.ai/search/modes) |
| `mode=fast` (v1) | not reachable yet | Sub-second | $1 | same |
| `mode=basic` (v1) | not reachable yet | Longer snippets, ~1 s | $5 | same |
| `mode=advanced` (v1, the default) | not reachable yet | Advanced retrieval, ~3 s | $5 | same |

web-router calls `/v1beta/search`, because only it took `max_results` when it
was mapped. Parallel's current modes are on `/v1`, where `max_results` sits
under `advanced_settings`; testing them needs the provider moved to `/v1`.
Results beyond 10 are +$1 per 1k. Up to 5,000 requests a month are free,
across Parallel's APIs rather than Search alone.

### You.com

| Mode | Setup | What it returns | Price per 1k | Source |
|---|---|---|---|---|
| No extraction (the default) | `you`, `you@lite` | Snippets | $5 | [pricing](https://you.com/pricing), [billing](https://you.com/docs/administration/billing.md) |
| `extraction_mode=highlights` | `you@fast` | Query-relevant passages | $5, no separate charge stated | [search guide](https://you.com/docs/guides/search) |
| `extraction_mode=full_page` | `you@deep` | The whole page | $5 + $1 per 1k pages fetched live | same |
| `full_page` from cache only | `you:full-page-cache` (unverified) | The whole page, cached copies only | $5 (cached pages are free) | same |
| Free MCP (`you-mcp`) | `you-mcp` | Search through the keyless endpoint | free, 100 queries a day | [MCP server](https://you.com/docs/build-with-agents/mcp-server) |

### Linkup

Prices are per call for `outputType=searchResults`, which is all web-router asks
for; `sourcedAnswer` and `structured` add $0.001 at every depth. 4,000 queries are free ([pricing page](https://www.linkup.so/pricing)).

| Mode | Setup | What it returns | Price per 1k | Source |
|---|---|---|---|---|
| `depth=flash` | `linkup:flash` | Snippets, about 0.5 s | $5 | [search overview](https://docs.linkup.so/pages/documentation/endpoints/search/overview.md) |
| `depth=fast` (sent on every search) | `linkup` | Snippets, about 1 s; Linkup's recommended default | $5 | same |
| `depth=standard` | `linkup:standard` | Snippets, one agentic pass for multi-topic queries | $5 | same |
| `depth=deep` | `linkup:deep` | Snippets, several search iterations, 6 to 16 s | $50 | same |

Latencies are from the 2026-09-30 live checks. All four depths returned snippets
of the same size (about 2.5K characters a result, 20 results by default), so
`deep` costs ten times as much for retrieval effort, not for more text. The
pricing page states only a range for search, $0.005 to $0.006 a request, which
does not cover `deep`; the docs page above is the source for this table.

### WebIQ

| Mode | Setup | What it returns | Price per 1k | Source |
|---|---|---|---|---|
| `contentFormat=passage` (sent on every search) | `webiq` | The passages of each page that answer the query | not published: limited access for select enterprise customers, by request | [Web IQ](https://www.microsoft.com/en-us/webiq), [announcement](https://blogs.bing.com/search/June-2026/Announcing-Microsoft-Web-IQ) |

"Grounding with Bing" ($14 per 1k) is a different product; its price does not
apply here.

### Keenable

Metered in credits, one per search or fetch; the first 100,000 requests a
month are free to every organization ([credits](https://docs.keenable.ai/credits.md)).
Past that, credit packages are priced per organization in its console, and no
list price is published. With no key, the `/public` endpoints are free and
capped at 1,000 requests an hour per IP ([rate limits](https://docs.keenable.ai/rate-limits.md)).

| Mode | Setup | What it returns | Price per 1k | Source |
|---|---|---|---|---|
| `mode=pro` (Keenable's default; web-router sends none) | `keenable` | Titles, URLs, a snippet per result | 100k a month free, then not published; $10 per 1k paid per request over x402 | [search](https://docs.keenable.ai/api-reference/search.md), [pay per request](https://docs.keenable.ai/pay-per-request.md) |
| `mode=realtime` | `keenable:realtime` | The same, faster; billed as its own SKU | same | same |

### DeepSeek

Per successful request: a search with no results, a failed request and a
timeout are free, and `count` does not change the price ([search and fetch](https://api-docs.deepseek.com/guides/search)).
The docs' free period covers only server-side tool calls through DeepSeek's
harness, Claude Code or Codex, until 2026-11-01 00:00 Beijing time (2026-10-31
16:00 UTC); web-router makes direct API calls, which it does not name as free. Search and Fetch each allow an account 5
requests a second.

| Mode | Setup | What it returns | Price per 1k | Source |
|---|---|---|---|---|
| default (`need_content` false) | `deepseek` | Titles, URLs, a snippet and a 600-800-character summary per result | $0.75 | [search and fetch](https://api-docs.deepseek.com/guides/search) |
| `need_content=true` | `deepseek:content` | The same, plus the page body | $0.75 | same |

### Keyless MCP search

| Backend | Setup | Price | Source |
|---|---|---|---|
| Exa MCP | `exa-mcp` | free, rate-limited; no limit published, over it answers 429 | [Exa MCP](https://exa.ai/docs/reference/exa-mcp) |
| Parallel Search MCP | `parallel-mcp` | free, "for exploration and light use"; no limit published | [quickstart](https://docs.parallel.ai/integrations/mcp/quickstart.md) |

You.com's keyless MCP is priced in its own section above.

## Extract (page reading)

| Backend | What web-router sends | Price | Source |
|---|---|---|---|
| Nimble | html (default) or markdown, no rendering | $1 per 1k URLs; rendering and higher drivers cost more, per-driver prices not published | [docs pricing](https://docs.nimbleway.com/nimble-sdk/admin/pricing) |
| Tavily | markdown | 1 credit per 5 URLs ($1.60 per 1k URLs), advanced 2 credits per 5 | [api credits](https://docs.tavily.com/documentation/api-credits) |
| Exa | text | $1 per 1k pages per content type | [pricing](https://exa.ai/pricing) |
| Firecrawl | markdown (default) or html | 1 credit per page; top-ups 1,000–5,000 credits per $5 depending on plan | [billing](https://docs.firecrawl.dev/billing.md), [pricing](https://www.firecrawl.dev/pricing.md) |
| Bright Data | html (default) or markdown | $1.50 per 1k requests pay-as-you-go, successes only | [Web Unlocker pricing](https://brightdata.com/pricing/web-unlocker) |
| Oxylabs | html (default) or markdown, no rendering | $0.95–1.15 per 1k results by plan; rendering $1.25–1.35 | [Web Scraper API pricing](https://oxylabs.io/products/scraper-api/web/pricing) |
| Jina Reader | markdown, keyless | free at 20 requests a minute keyless; with a key, billed by output tokens: 10M free (non-commercial), then $50 per 1B tokens (Standard) or $500 per 11B (Premium), varies by location | [Jina Reader](https://jina.ai/reader) |
| Parallel | not used for extract in web-router | $1 per 1k results | [pricing](https://parallel.ai/pricing) |
| WebIQ (Browse) | markdown or text, `maxLength` 500,000 | not published: limited access for select enterprise customers, by request | [Web IQ](https://webiq.microsoft.ai), [SDK docs](https://webiq.microsoft.ai/documentation/sdk) |
| Keenable | markdown, `max_chars` 500,000 | one credit per fetch, 100k requests a month free, then not published; keyless free at 1,000 an hour per IP | [credits](https://docs.keenable.ai/credits.md), [fetch](https://docs.keenable.ai/api-reference/fetch.md) |
| DeepSeek | markdown | $0.75 per 1k successful fetches | [search and fetch](https://api-docs.deepseek.com/guides/search) |
| Linkup | markdown (default) or html (`includeRawContent`), `mode=standard`, no rendering | $0.001 per call ($1 per 1k); `renderJs` or `mode=pro` $0.005, both $0.01; a `schema` adds $0.001 | [fetch overview](https://docs.linkup.so/pages/documentation/endpoints/fetch/overview.md) |

## Research

Not benchmarked and not reachable from a search setup; listed because a run bills
by the run (or, for Perplexity, by its tokens and tool calls) rather than by the
request. Each row is the vendor tier web-router's normalized `effort` maps to
(`NIMBLE_RESEARCH_EFFORT`, `PARALLEL_RESEARCH_PROCESSORS`,
`PERPLEXITY_RESEARCH_PRESETS`, `LINKUP_RESEARCH_DEPTHS`); checked 2026-10-07.

| Backend | `effort` | Vendor setting | Price | Source |
|---|---|---|---|---|
| Nimble | `low` | Web Search Agent `effort=low`, 10 to 30 s | $0.025 per run | [efforts](https://docs.nimbleway.com/nimble-sdk/web-search-agents/efforts.md), [pricing](https://docs.nimbleway.com/nimble-sdk/admin/pricing.md) |
| Nimble | `medium` | `medium`, 1 to 3 minutes | $0.10 per run | same |
| Nimble | `high` | `high`, 5 to 15 minutes | $0.50 per run | same |
| Nimble | `max` | `max` | not published ("custom, coming soon") | same |
| Parallel | `low` | processor `lite` | $0.005 per run ($5 per 1k) | [pricing](https://docs.parallel.ai/getting-started/pricing) |
| Parallel | `medium` (the default) | `core` | $0.025 per run | same |
| Parallel | `high` | `pro` | $0.10 per run | same |
| Parallel | `max` | `ultra` | $0.30 per run | same |
| Perplexity | `low` | preset `low`: `openai/gpt-6-luna`, up to 5 steps | $0.10 / $0.50 per 1M input / output tokens, plus tools | [presets](https://docs.perplexity.ai/docs/agent-api/presets.md), [models](https://docs.perplexity.ai/docs/agent-api/models.md) |
| Perplexity | `medium` (the default) | preset `medium`: `gpt-6-luna`, up to 15 steps | $0.10 / $0.50 per 1M tokens, plus tools | same |
| Perplexity | `high` | preset `high`: `openai/gpt-6-sol`, up to 15 steps | $2.00 / $10.00 per 1M tokens, plus tools | same |
| Perplexity | `max` | preset `xhigh`: `anthropic/claude-opus-5-5`, up to 100 steps | $4.00 / $20.00 per 1M tokens, plus tools | same; tool fees on [pricing](https://docs.perplexity.ai/getting-started/pricing) |
| Linkup | `low` | `reasoningDepth=S`, 2 to 5 minutes | $0.25 per run | [research overview](https://docs.linkup.so/pages/documentation/endpoints/research/overview.md) |
| Linkup | `medium` | `M`, 3 to 7 minutes | $0.50 per run | same |
| Linkup | `high` (Linkup's own default) | `L`, 5 to 10 minutes | $1.50 per run | same |
| Linkup | `max` | `XL`, 10 to 20 minutes | $2.50 per run | same |

**Perplexity has no per-run price.** A run costs its tokens plus each tool call:
`web_search` $0.001 (fast, low and medium presets) or $0.0025 (standard),
`fetch_url` $0.0005, `finance_search` $0.005, a `sandbox` session $0.03 per
20 minutes. The presets are unversioned and may switch models; the pricing
page's calculator still names the older `gpt-5.6-luna` and `gpt-5.6-sol`
($0.20 / $1.20 and $4 / $20), and its per-run figures are "median values … NOT
billed values". Past 272K input tokens the OpenAI models' input price doubles and
the output price rises by half: `gpt-6-luna` $0.20 / $0.75, `gpt-6-sol` $4 / $15
per 1M tokens.

Tiers web-router does not map, reachable only through `provider_options`: Nimble
`x-high` ($2.00 per run), Parallel `base` ($0.01) and `ultra2x` / `4x` / `8x`
($0.60 / $1.20 / $2.40). Parallel bills only completed runs. The one live Linkup
run, at `effort=low`, took about 105 seconds.

## Browser sessions

Billed by the browser-hour while a session is open, plus proxy traffic. The
`BROWSER_TIMEOUT` default (10 minutes) bounds a session nobody released.

| Vendor | Browser time | Proxy | Other | Source |
|---|---|---|---|---|
| Steel | $0.10 per hour (Launch: no fee, $30 one-time credit); $0.08 (Scale: $250/mo) | $10 / $6 per GB | CAPTCHA solves $3 / $1 per 1k; stealth is Enterprise-only (web-router does not map it for Steel) | [pricing and limits](https://docs.steel.dev/overview/pricinglimits) |
| Browserbase | $0.12 per hour past the included hours (Developer $20/mo, 100 h); $0.10 (Startup $99/mo, 500 h) | $12 / $10 per GB past 1 / 5 GB included; none on Free | advanced stealth (web-router's `stealth="high"`) only on Scale, custom price | [pricing](https://www.browserbase.com/pricing) |
| Hyperbrowser | $0.10 per hour (100 credits; 1 credit = $0.001) | $10 per GB | plan tiers and stealth prices not published on a page that could be read; `stealth="high"` maps to ultra stealth | [pricing docs](https://hyperbrowser.ai/docs/pricing) |
| Anchor | $0.05 per hour, plus $0.01 per browser created | $8 per GB (Anchor's proxy); $0.20 per GB own or none | plans Free ($5 credit) to Growth ($2,000/mo); stealth and captcha are plan features. The pricing page's worked example shows $0.09 an hour and no creation fee: recheck before quoting | [pricing docs](https://docs.anchorbrowser.io/pricing), [pricing page](https://anchorbrowser.io/pricing) |
| local | free: the caller's own Chromium | — | — | — |

## Browse tasks (hosted browser agents)

| Vendor | What web-router calls | Price | Source |
|---|---|---|---|
| Browser Use | legacy v2 `/api/v2/tasks` | $0.01 per task, plus per agent step by model: `browser-use-2.0` (the default) $0.006, `gemini-flash-lite-latest` $0.005, `gemini-flash-latest` $0.0075, `o3` $0.03, Claude Sonnet $0.05; browser time $0.02 per hour and residential proxy $5 per GB are billed on top for Cloud | [legacy agent](https://docs.browser-use.com/cloud/legacy/agent), [pricing](https://browser-use.com/pricing) |
| Skyvern | `/v1/run/tasks` | credits by plan: 5,000 free once; Hobby $29/mo for 30k; Pro $149/mo for 150k (residential proxy, advanced captcha). No dollar price per action or overage published, and the pages disagree on what a credit buys | [pricing](https://www.skyvern.com/pricing), [billing](https://skyvern.com/docs/cloud/account-settings/billing-usage) |
| Browserbase | Agents API `/v1/agents/runs` | 3 / 15 / 50 runs included (Free / Developer / Startup), custom on Scale; no price per run past those, and model tokens are billed at "market price" with no figure | [pricing](https://www.browserbase.com/pricing) |

The model a browse agent runs on goes through `provider_options`, so its price
depends on that choice. Browser Use's current v4 API bills model tokens at the
provider's rate plus 20%; web-router does not call it.

## Agent platforms

The agent benchmark's other side of the bill, for reading its reports.

| Platform | What | Price | Source |
|---|---|---|---|
| Anthropic | Session list cost | Stated by Anthropic per session; the benchmark reports it as given | — |
| OpenAI gpt-5 | Input / cached input / output tokens | $1.25 / $0.125 / $10 per million | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| OpenAI web search | Built-in `web_search` tool calls | $10 per 1k calls, plus the pages it reads at model rates | same |
