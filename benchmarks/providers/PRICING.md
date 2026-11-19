# Provider pricing and modes

A reference for reading benchmark results against cost: what every backend
web-router calls costs at list price, for each method (search, extract,
research, browser sessions and browse tasks), and how a benchmark setup reaches
each search mode.

**The tables are generated; the prose is not.** Every price table here sits
between `<!-- prices: ... -->` markers and is written from
[`src/web_router/data/prices.json`](../../src/web_router/data/prices.json), the
price list the library ships and `web_router.prices` reads, so the router can
estimate what a call costs before it runs. To change a price, edit that file and
run `make prices`; a test fails while a table and the data disagree. The prose
around the tables is hand-written. Every figure is an **estimated** list price:
dated and sourced so it can be checked and redone, never a quote.

**Checked 2026-10-07** from each vendor's own docs and pricing pages (search
and extract first checked 2026-09-30), unless a row's Checked column says
otherwise. Prices are pay-as-you-go list prices in US dollars; a vendor that
publishes no price is marked "not published" (`unknown` in the data) rather
than estimated, and one that bills by tokens has no per-call figure
(`token_billed`); volume plans and negotiated rates differ. Recheck a source
before quoting a figure, and update the row's `checked` date when you do.

**Mode** is the data's key: `default` for a call that sets nothing,
web-router's own `search_depth` or `effort` value, or a vendor mode a provider
class declares, spelled with the backend's name first (`tavily-ultra-fast`), as
`web_router.modes` reads a mode id. Marked "(default)", it is what a
call that sets nothing gets.

**How a setup reaches a mode.** A setup is the id `provider=` pins:
`provider:lite|fast|deep` uses web-router's normalized `search_depth`, where the
backend maps it, and `provider:<vendor part>` (`tavily:tavily-ultra-fast`) sends
the wire the provider class declares for it. A mode marked unverified has not
yet been shown by a live call to do what its docs say.

## Search

### Nimble

<!-- prices: method=search provider=nimble -->
| Mode | Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|---|
| `lite` | `nimble:lite` | `search_depth=lite` | Titles, URLs, snippets | $1.10 per 1k | [docs.nimbleway.com](https://docs.nimbleway.com/nimble-sdk/admin/pricing) | 2026-10-07 |
| `fast` (default) | `nimble`, `nimble:fast` | `search_depth=standard` (the default) | ~2K characters per result | $5 per 1k; standard depth | [docs.nimbleway.com](https://docs.nimbleway.com/nimble-sdk/admin/pricing) | 2026-10-07 |
| `deep` | `nimble:deep` | `+ full_content=true` | The whole page, scraped live | $15 per 1k; estimate is the derived upper bound: Nimble states $5 per 1k at standard depth plus $1 per 1k URLs for `full_content`, which web-router always sends at this depth, and Nimble does not state its default result count, so this assumes 10 results: $5 + 10 × $1 = $15 per 1k (derived, not stated by Nimble) | [docs.nimbleway.com](https://docs.nimbleway.com/nimble-sdk/admin/pricing) | 2026-10-07 |
<!-- /prices -->

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

<!-- prices: method=search provider=tavily -->
| Mode | Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|---|
| `lite` (default) | `tavily`, `tavily:lite` | `search_depth=basic` (the default) | Several snippets per URL | $8 per 1k; 1 credit at $0.008 | [docs.tavily.com](https://docs.tavily.com/documentation/api-reference/endpoint/search) | 2026-10-07 |
| `fast` | `tavily:fast` | `search_depth=advanced` | Most relevant snippets, slower | $16 per 1k; 2 credits | [docs.tavily.com](https://docs.tavily.com/documentation/api-reference/endpoint/search) | 2026-10-07 |
| `deep` | `tavily:deep` | `advanced + include_raw_content` | Adds the page body | $16 per 1k; 2 credits | [docs.tavily.com](https://docs.tavily.com/documentation/api-reference/endpoint/search) | 2026-10-07 |
| `tavily-fast` | `tavily:tavily-fast` (unverified) | `search_depth=fast` | Lower latency | $8 per 1k; 1 credit | [docs.tavily.com](https://docs.tavily.com/documentation/api-reference/endpoint/search) | 2026-10-07 |
| `tavily-ultra-fast` | `tavily:tavily-ultra-fast` (unverified) | `search_depth=ultra-fast` | Lowest latency, one summary per URL | $8 per 1k; 1 credit | [docs.tavily.com](https://docs.tavily.com/documentation/api-reference/endpoint/search) | 2026-10-07 |
<!-- /prices -->

`auto_parameters=true` (Tavily picks the settings) costs 2 credits. Plans:
Project 4,000 credits for $30, Bootstrap 15,000 for $100, Startup 38,000 for
$220, Growth 100,000 for $500.

### Exa

<!-- prices: method=search provider=exa -->
| Mode | Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|---|
| default | `exa` | `type=auto` (the default) | Balances quality and speed | $17 per 1k; estimate is the derived upper bound: Exa states $7 per 1k plus contents at $1 per 1k pages per content type, and web-router asks for text on 5–10 results, so roughly $12–17 per 1k (derived, not stated by Exa) | [exa.ai](https://exa.ai/pricing) | 2026-10-07 |
| `exa-highlights` | `exa:exa-highlights` | `contents.highlights` instead of text | Query-relevant highlights instead of page text | $17 per 1k; estimate is the derived upper bound: Exa states $7 per 1k plus contents at $1 per 1k pages per content type, and web-router asks for text on 5–10 results, so roughly $12–17 per 1k (derived, not stated by Exa) | [exa.ai](https://exa.ai/pricing) | 2026-10-07 |
| `exa-instant` | `exa:exa-instant` (unverified) | `type=instant` | Fastest | $14 per 1k; estimate is the derived upper bound: Exa states $4 per 1k plus contents at $1 per 1k pages per content type, and web-router asks for text on 5–10 results, so roughly $9–14 per 1k (derived, not stated by Exa) | [exa.ai](https://exa.ai/pricing) | 2026-10-07 |
| `exa-fast` | `exa:exa-fast` (unverified) | `type=fast` | Low latency | $17 per 1k; estimate is the derived upper bound: Exa states $7 per 1k plus contents at $1 per 1k pages per content type, and web-router asks for text on 5–10 results, so roughly $12–17 per 1k (derived, not stated by Exa) | [exa.ai](https://exa.ai/pricing) | 2026-10-07 |
| `exa-deep-lite` | `exa:exa-deep-lite` (unverified) | `type=deep-lite` | Light research with synthesis, ~4s | $22 per 1k; estimate is the derived upper bound: Exa states $12 per 1k plus contents at $1 per 1k pages per content type, and web-router asks for text on 5–10 results, so roughly $17–22 per 1k (derived, not stated by Exa) | [exa.ai](https://exa.ai/pricing) | 2026-10-07 |
| `exa-deep` | `exa:exa-deep` (unverified) | `type=deep` | Multi-step research | $22 per 1k; estimate is the derived upper bound: Exa states $12 per 1k plus contents at $1 per 1k pages per content type, and web-router asks for text on 5–10 results, so roughly $17–22 per 1k (derived, not stated by Exa) | [exa.ai](https://exa.ai/pricing) | 2026-10-07 |
| `exa-deep-reasoning` | `exa:exa-deep-reasoning` (unverified) | `type=deep-reasoning` | Stronger reasoning | $25 per 1k; estimate is the derived upper bound: Exa states $15 per 1k plus contents at $1 per 1k pages per content type, and web-router asks for text on 5–10 results, so roughly $20–25 per 1k (derived, not stated by Exa) | [exa.ai](https://exa.ai/pricing) | 2026-10-07 |
<!-- /prices -->

Contents are billed per page per content type, $1 per 1k each: text, highlights
or summary. web-router always asks for text on 5–10 results, so a default
search costs about $7 + $1 × results per 1k, **roughly $12–17 per 1k** (derived
from the stated prices, not stated by Exa). `exa:highlights` swaps text for
highlights at the same rate. Results beyond 10 are +$1 per 1k.

### Perplexity (Search API)

<!-- prices: method=search provider=perplexity -->
| Mode | Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|---|
| default | `perplexity` | `search_type=web` (the default) | General web search | $5 per 1k | [docs.perplexity.ai](https://docs.perplexity.ai/getting-started/pricing) | 2026-10-07 |
| `perplexity-fast` | `perplexity:perplexity-fast` (unverified) | `search_type=fast` | Lower latency, lower cost | $1 per 1k | [docs.perplexity.ai](https://docs.perplexity.ai/getting-started/pricing) | 2026-10-07 |
<!-- /prices -->

A request with several queries is billed as one request.

### Brave

<!-- prices: method=search provider=brave -->
| Mode | Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|---|
| default | `brave` | Search plan | Full web results | $5 per 1k; $5 free credit per month | [brave.com](https://brave.com/search/api/) | 2026-10-07 |
| `brave-extra-snippets` | `brave:brave-extra-snippets` (unverified) | `extra_snippets=true` | Up to 5 extra excerpts per result | $5 per 1k; included in the search price | [api-dashboard.search.brave.com](https://api-dashboard.search.brave.com/app/documentation/web-search/query) | 2026-10-07 |
<!-- /prices -->

The Answers plan (summarized, cited answers) is priced under [Answer](#answer).
The LLM Context endpoint (ranked page chunks for an LLM) has no stated price.

### Parallel

<!-- prices: method=search provider=parallel -->
| Mode | Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|---|
| default | `parallel` | `mode=fast` on `/v1/search` | What web-router calls: fast pinned, Parallel's replacement for the beta default; latency measured on /v1beta | fast lists $1 per 1k; left unknown until route-eval re-runs on /v1, since a price reorders auto's chain | [docs.parallel.ai](https://docs.parallel.ai/search/modes) | 2026-11-09 |
| `parallel-turbo` | `parallel:parallel-turbo` (unverified) | `mode=turbo` (v1) | ~200 ms, English and Japanese only | $1 per 1k | [docs.parallel.ai](https://docs.parallel.ai/search/modes) | 2026-11-09 |
| `parallel-basic` | `parallel:parallel-basic` (unverified) | `mode=basic` (v1) | Longer snippets, ~1 s | $5 per 1k | [docs.parallel.ai](https://docs.parallel.ai/search/modes) | 2026-11-09 |
| `parallel-advanced` | `parallel:parallel-advanced` (unverified) | `mode=advanced` (/v1's own default; web-router sends `fast`) | Advanced retrieval, ~3 s | $5 per 1k | [docs.parallel.ai](https://docs.parallel.ai/search/modes) | 2026-11-09 |
<!-- /prices -->

web-router calls `/v1/search` with `mode=fast` pinned: unset, `/v1` runs
`advanced`, the beta's agentic tier, at five times the price. Parallel's
migration guide maps the beta's default, `one-shot`, to `fast`. The other modes
go through `provider_options={"mode": ...}`; the latency above was measured on
`/v1beta`, before the move.
Results beyond 10 are +$1 per 1k. Up to 5,000 requests a month are free,
across Parallel's APIs rather than Search alone.

### You.com

<!-- prices: method=search provider=you,you-mcp -->
| Backend | Mode | Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|---|---|
| you | `lite` (default) | `you`, `you:lite` | No extraction (the default) | Snippets | $5 per 1k | [you.com](https://you.com/pricing) | 2026-10-07 |
| you | `fast` | `you:fast` | `extraction_mode=highlights` | Query-relevant passages | $5 per 1k; no separate charge stated | [you.com](https://you.com/docs/guides/search) | 2026-10-07 |
| you | `deep` | `you:deep` | `extraction_mode=full_page` | The whole page | $15 per 1k; estimate is the derived upper bound: You.com states $5 per 1k plus $1 per 1k pages fetched live, `full_page` can fetch every result live, and an unset `max_results` gets You.com's default of 10 results, so $5 + 10 × $1 = $15 per 1k (derived, not stated by You.com) | [you.com](https://you.com/docs/guides/search) | 2026-10-07 |
| you | `you-full-page-cache` | `you:you-full-page-cache` (unverified) | `full_page` from cache only | The whole page, cached copies only | $5 per 1k; cached pages are free | [you.com](https://you.com/docs/guides/search) | 2026-10-07 |
| you-mcp | default | `you-mcp` | Free MCP (`you-mcp`) | Search through the keyless endpoint | free; 100 queries a day | [you.com](https://you.com/docs/build-with-agents/mcp-server) | 2026-10-07 |
<!-- /prices -->

### Linkup

Prices are per call for `outputType=searchResults`, which is all web-router asks
for; `sourcedAnswer` and `structured` add $0.001 at every depth. 4,000 queries are free ([pricing page](https://www.linkup.so/pricing)).

<!-- prices: method=search provider=linkup -->
| Mode | Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|---|
| `linkup-flash` | `linkup:linkup-flash` | `depth=flash` | Snippets, about 0.5 s | $5 per 1k | [docs.linkup.so](https://docs.linkup.so/pages/documentation/endpoints/search/overview.md) | 2026-10-07 |
| default | `linkup` | `depth=fast` (sent on every search) | Snippets, about 1 s; Linkup's recommended default | $5 per 1k | [docs.linkup.so](https://docs.linkup.so/pages/documentation/endpoints/search/overview.md) | 2026-10-07 |
| `linkup-standard` | `linkup:linkup-standard` | `depth=standard` | Snippets, one agentic pass for multi-topic queries | $5 per 1k | [docs.linkup.so](https://docs.linkup.so/pages/documentation/endpoints/search/overview.md) | 2026-10-07 |
| `linkup-deep` | `linkup:linkup-deep` | `depth=deep` | Snippets, several search iterations, 6 to 16 s | $50 per 1k | [docs.linkup.so](https://docs.linkup.so/pages/documentation/endpoints/search/overview.md) | 2026-10-07 |
<!-- /prices -->

Latencies are from the 2026-09-30 live checks. All four depths returned snippets
of the same size (about 2.5K characters a result, 20 results by default), so
`deep` costs ten times as much for retrieval effort, not for more text. The
pricing page states only a range for search, $0.005 to $0.006 a request, which
does not cover `deep`; the docs page above is the source for this table.

### WebIQ

<!-- prices: method=search provider=webiq -->
| Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|
| `webiq` | `contentFormat=passage` (sent on every search) | The passages of each page that answer the query | not published: limited access for select enterprise customers, by request | [www.microsoft.com](https://www.microsoft.com/en-us/webiq) | 2026-10-07 |
<!-- /prices -->

"Grounding with Bing" ($14 per 1k) is a different product; its price does not
apply here.

### Keenable

Metered in credits, one per search or fetch; the first 100,000 requests a
month are free to every organization ([credits](https://docs.keenable.ai/credits.md)).
Past that, pay as you go is $4 per 1,000 requests, and dedicated capacity at
100+ requests a second is $1 per 1,000 ([pricing](https://keenable.ai/pricing));
web-router estimates at $4. With no key, the `/public` endpoints are free and
capped at 1,000 requests an hour per IP ([rate limits](https://docs.keenable.ai/rate-limits.md)).

<!-- prices: method=search provider=keenable -->
| Mode | Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|---|
| default | `keenable` | `mode=pro` (Keenable's default; web-router sends none) | Titles, URLs, a snippet per result | $4 per 1k; one credit per search; 100k requests a month free, then $4 per 1k pay as you go ($1 per 1k on dedicated capacity at 100+ RPS); $10 per 1k paid per request over x402; keyless free at 1,000 an hour per IP | [keenable.ai](https://keenable.ai/pricing) | 2026-11-18 |
| `keenable-realtime` | `keenable:keenable-realtime` | `mode=realtime` | The same, faster; its own SKU, at one credit like the default | $4 per 1k; one credit per search; 100k requests a month free, then $4 per 1k pay as you go ($1 per 1k on dedicated capacity at 100+ RPS); $10 per 1k paid per request over x402; keyless free at 1,000 an hour per IP | [keenable.ai](https://keenable.ai/pricing) | 2026-11-18 |
<!-- /prices -->

### DeepSeek

Per successful request: a search with no results, a failed request and a
timeout are free, and `count` does not change the price ([search and fetch](https://api-docs.deepseek.com/guides/search)).
The docs' free period covers only server-side tool calls through DeepSeek's
harness, Claude Code or Codex, until 2026-11-01 00:00 Beijing time (2026-10-31
16:00 UTC); web-router makes direct API calls, which it does not name as free. Search and Fetch each allow an account 5
requests a second.

<!-- prices: method=search provider=deepseek -->
| Mode | Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|---|
| default | `deepseek` | default (`need_content` false) | Titles, URLs, a snippet and a summary per result | $0.75 per 1k; successful requests only | [api-docs.deepseek.com](https://api-docs.deepseek.com/guides/search) | 2026-10-19 |
| `deepseek-content` | `deepseek:deepseek-content` | `need_content=true` | The same, plus the page body | $0.75 per 1k; successful requests only | [api-docs.deepseek.com](https://api-docs.deepseek.com/guides/search) | 2026-10-19 |
<!-- /prices -->

### Valyu

Per result, not per request: a web or news result is 0.1 credit, and $10 buys
1,600 credits pay-as-you-go, so a search of 10 web results costs about $0.006
([pricing](https://docs.valyu.ai/pricing)). The default of 5 results costs half
that. web-router sends `search_type=web`; Valyu's own default also searches
datasets, priced per 1,000 results at up to $30. Accounts get free credits each
month.

<!-- prices: method=search provider=valyu -->
| Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|
| `valyu` | default (`search_type=web`) | Titles, URLs, a relevance score and the page body per result | $6.25 per 1k; searches of 10 web results: 0.1 credit per result, at $10 per 1,600 credits pay-as-you-go; dataset results cost more | [docs.valyu.ai](https://docs.valyu.ai/pricing) | 2026-11-12 |
<!-- /prices -->

### Seltz

Per search, however many documents it returns ([pricing](https://docs.seltz.ai/pricing)): $5 per 1k on
the Pro tier, which Seltz uses unless `tier` says otherwise, and $3 per 1k on Base. New accounts get $20
of credit.

<!-- prices: method=search provider=seltz -->
| Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|
| `seltz` | default (`tier=pro`) | URLs, dates and the document body per result; no titles | $5 per 1k; Pro tier, the default; $3 per 1k with `tier=base` | [docs.seltz.ai](https://docs.seltz.ai/pricing) | 2026-11-12 |
<!-- /prices -->

### Octen

Per call ([pricing](https://docs.octen.ai/overview/pricing)): Web Search lists at $5 per 1k, currently
$1. Full content, which `search_depth` `fast` and `deep` turn on, adds $0.50 per 1k results. New accounts get $5
of balance, and a payment method is required to use the API.

<!-- prices: method=search provider=octen -->
| Mode | Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|---|
| default | `octen` | default (highlights, no `full_content`) | Titles, URLs, dates and a query-relevant highlight per result | $1 per 1k; 80% off the $5 list price; full content $0.50 per 1k results on top | [docs.octen.ai](https://docs.octen.ai/overview/pricing) | 2026-11-12 |
| `fast` | `octen:fast` | `full_content.enable=true` | Adds each result's page body | $1 per 1k; full content is free for the first 10 results, then $0.50 per 1k results | [docs.octen.ai](https://docs.octen.ai/overview/pricing) | 2026-11-12 |
| `deep` | `octen:deep` | `full_content` up to 100,000 tokens | Adds a longer page body per result | $1 per 1k; full content is free for the first 10 results, then $0.50 per 1k results | [docs.octen.ai](https://docs.octen.ai/overview/pricing) | 2026-11-12 |
<!-- /prices -->

### Keirolabs

Per search, however many results it returns ([pricing](https://keirolabs.cloud/pricing)): $0.10 per 1k on
the Essential plan ($29.99 a month, 300,000 searches included), $0.06 per 1k on Pro and $0.03 per 1k on
Startup.

<!-- prices: method=search provider=keirolabs -->
| Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|
| `keirolabs` | default (lite search) | Titles, URLs and a snippet per result; no page body or date | $0.10 per 1k; Essential plan ($29.99 a month, 300k searches included); $0.06 per 1k on Pro, $0.03 per 1k on Startup | [keirolabs.cloud](https://keirolabs.cloud/pricing) | 2026-11-12 |
<!-- /prices -->

### SpecStream

Per call ([agents.md](https://specstream.ai/agents.md)): $5 per 1k searches of up to 10 results, and each started
block of 10 more results adds $6 per 1k, so 100 results are $59 per 1k. With `speed=fast` through
`provider_options`, $1 per 1k searches of up to 10 results. A search that returns no result is not charged.

<!-- prices: method=search provider=specstream -->
| Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|
| `specstream` | default | Titles, URLs, a snippet and a date per result; an answer and rows in `raw` | $5 per 1k; searches of up to 10 results; each started block of 10 more adds $6 per 1k; $1 per 1k with `speed=fast` through provider_options | [specstream.ai](https://specstream.ai/agents.md) | 2026-11-19 |
<!-- /prices -->

### AWS AgentCore Web Search

Billed by AWS to the account that owns the gateway: per query, plus the
gateway's own fee per tool invocation. An idle gateway costs nothing
([AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/)). No mode
is registered for the scan yet: AWS's terms forbid storing results in bulk, so
check what a run keeps before adding one.

<!-- prices: method=search provider=agentcore -->
| Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|
| `agentcore` | default | Titles, URLs, a passage and a published date per result, up to 25 | $7 per 1k; plus $0.005 per 1k gateway invocations | [aws.amazon.com](https://aws.amazon.com/bedrock/agentcore/pricing/) | 2026-10-27 |
<!-- /prices -->

### Keyless MCP search

<!-- prices: method=search provider=exa-mcp,parallel-mcp -->
| Backend | Setup | Estimated price | Source | Checked |
|---|---|---|---|---|
| exa-mcp | `exa-mcp` | free; rate-limited; no limit published, over it answers 429 | [exa.ai](https://exa.ai/docs/reference/exa-mcp) | 2026-10-07 |
| parallel-mcp | `parallel-mcp` | free; "for exploration and light use"; no limit published | [docs.parallel.ai](https://docs.parallel.ai/integrations/mcp/quickstart.md) | 2026-10-07 |
<!-- /prices -->

You.com's keyless MCP is priced in its own section above.

### Search through a LiteLLM proxy

Exa, Tavily and Parallel search sent through a LiteLLM proxy's `/v1/search`, which bills each call to the LiteLLM
key at the proxy's own cost map. These are LiteLLM's published defaults; an operator may set others.

<!-- prices: method=search provider=exa-litellm,tavily-litellm,parallel-litellm -->
| Backend | Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|---|
| exa-litellm | `exa-litellm` | Exa's own mapping, sent through the proxy | Exa search, billed to the LiteLLM key | $5 per 1k; LiteLLM's cost map bills `exa_ai/search` per query: $0.005 for up to 25 results, $0.025 above | [github.com](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json) | 2026-11-12 |
| tavily-litellm | `tavily-litellm` | Tavily's own mapping, sent through the proxy | Tavily search, billed to the LiteLLM key | $8 per 1k; LiteLLM's cost map: `tavily/search` $0.008 a query, `tavily/search-advanced` $0.016 | [github.com](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json) | 2026-11-12 |
| parallel-litellm | `parallel-litellm` | Parallel's own mapping, sent through the proxy | Parallel search, billed to the LiteLLM key | $5 per 1k; LiteLLM's cost map: `parallel_ai/search` $0.005 a query | [github.com](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json) | 2026-11-12 |
<!-- /prices -->

## Extract (page reading)

<!-- prices: method=extract -->
| Backend | Mode | Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|---|---|
| nimble | default | — | html (default) or markdown, no rendering | — | $1 per 1k; per URL; rendering and higher drivers cost more, per-driver prices not published | [docs.nimbleway.com](https://docs.nimbleway.com/nimble-sdk/admin/pricing) | 2026-10-07 |
| nimble | `nimble-vx8` | `nimble:nimble-vx8` (unverified) | `driver=vx8` | Headless browser | per-driver prices not published | [docs.nimbleway.com](https://docs.nimbleway.com/nimble-sdk/admin/pricing) | 2026-11-17 |
| nimble | `nimble-vx8-pro` | `nimble:nimble-vx8-pro` (unverified) | `driver=vx8-pro` | Headful browser | per-driver prices not published | [docs.nimbleway.com](https://docs.nimbleway.com/nimble-sdk/admin/pricing) | 2026-11-17 |
| nimble | `nimble-vx10` | `nimble:nimble-vx10` (unverified) | `driver=vx10` | Stealth headless browser | per-driver prices not published | [docs.nimbleway.com](https://docs.nimbleway.com/nimble-sdk/admin/pricing) | 2026-11-17 |
| nimble | `nimble-vx10-pro` | `nimble:nimble-vx10-pro` (unverified) | `driver=vx10-pro` | Stealth headful browser | per-driver prices not published | [docs.nimbleway.com](https://docs.nimbleway.com/nimble-sdk/admin/pricing) | 2026-11-17 |
| nimble | `nimble-max` | `nimble:nimble-max` (unverified) | `driver=max` | Every driver at once, the first success wins | $2.50 per 1k; only the success is charged | [docs.nimbleway.com](https://docs.nimbleway.com/nimble-sdk/admin/pricing) | 2026-11-17 |
| tavily | default | — | markdown | — | $8 per 1k; estimate is the worst case, one credit ($0.008) per URL: Tavily bills 1 credit per 5 successful URLs (advanced 2 per 5) and failed URLs are free, but does not say how a partial group of five is billed, so a request with 1–4 URLs may cost a whole credit; full groups of five cost $1.60 per 1k | [docs.tavily.com](https://docs.tavily.com/documentation/api-credits) | 2026-10-07 |
| tavily | `tavily-advanced` | `tavily:tavily-advanced` (unverified) | `extract_depth=advanced` | Tables and embedded content too | $16 per 1k; worst case, two credits ($0.016) per URL: Tavily bills 2 credits per 5 successful URLs at advanced; full groups of five cost $3.20 per 1k | [docs.tavily.com](https://docs.tavily.com/documentation/api-credits) | 2026-11-17 |
| exa | default | — | text | — | $1 per 1k; per page per content type | [exa.ai](https://exa.ai/pricing) | 2026-10-07 |
| exa | `exa-highlights` | `exa:exa-highlights` (unverified) | `highlights=true` | Highlights beside the text | $2 per 1k; $1 per 1k pages per content type, text included | [exa.ai](https://exa.ai/pricing) | 2026-11-17 |
| exa | `exa-summary` | `exa:exa-summary` (unverified) | `summary=true` | A summary beside the text | $2 per 1k; $1 per 1k pages per content type, text included | [exa.ai](https://exa.ai/pricing) | 2026-11-17 |
| firecrawl | default | — | markdown (default) or html | — | $5 per 1k; 1 credit per page; top-ups 1,000–5,000 credits per $5 depending on plan; the estimate takes the top, $5 per 1k | [www.firecrawl.dev](https://www.firecrawl.dev/pricing.md) | 2026-10-07 |
| brightdata | default | — | html (default) or markdown | — | $1.50 per 1k; pay-as-you-go, successes only | [brightdata.com](https://brightdata.com/pricing/web-unlocker) | 2026-10-07 |
| oxylabs | default | — | html (default) or markdown, no rendering | — | $1 per 1k; custom-domain scraping without JavaScript, $0.75–1.00 per 1k results by volume; the estimate takes the top | [oxylabs.io](https://oxylabs.io/products/scraper-api/web/pricing) | 2026-11-18 |
| oxylabs | `oxylabs-render` | `oxylabs:oxylabs-render` (unverified) | `render=html` | The page rendered in a browser first | $1.50 per 1k; custom-domain JavaScript rendering, $1.22–1.50 per 1k results by volume; the estimate takes the top | [oxylabs.io](https://oxylabs.io/products/scraper-api/web/pricing) | 2026-11-17 |
| jina | default | — | markdown, keyless | — | billed by tokens; free at 20 requests a minute keyless; with a key, billed by output tokens: 10M free (non-commercial), then $50 per 1B tokens (Standard) or $500 per 11B (Premium), varies by location | [jina.ai](https://jina.ai/reader) | 2026-10-07 |
| parallel | default | — | not used for extract in web-router | — | $1 per 1k; per result | [parallel.ai](https://parallel.ai/pricing) | 2026-10-07 |
| webiq | default | — | markdown or text, `maxLength` 500,000 | — | Browse; not published: limited access for select enterprise customers, by request | [webiq.microsoft.ai](https://webiq.microsoft.ai) | 2026-11-01 |
| keenable | default | — | markdown, `max_chars` 500,000 | — | $4 per 1k; one credit per fetch; 100k requests a month free, then $4 per 1k pay as you go ($1 per 1k on dedicated capacity at 100+ RPS); $10 per 1k paid per request over x402; keyless free at 1,000 an hour per IP | [keenable.ai](https://keenable.ai/pricing) | 2026-11-18 |
| deepseek | default | — | markdown | — | $0.75 per 1k; successful fetches only | [api-docs.deepseek.com](https://api-docs.deepseek.com/guides/search) | 2026-10-19 |
| linkup | default | — | markdown (default) or html (`includeRawContent`), `mode=standard`, no rendering | — | $0.001 per request; $1 per 1k; `renderJs` or `mode=pro` $0.005, both $0.01; a `schema` adds $0.001 | [docs.linkup.so](https://docs.linkup.so/pages/documentation/endpoints/fetch/overview.md) | 2026-10-07 |
| linkup | `linkup-pro` | `linkup:linkup-pro` (unverified) | `mode=pro` | Higher success on hard pages | $0.005 per request; $5 per 1k; with `renderJs` $0.01 | [docs.linkup.so](https://docs.linkup.so/pages/documentation/development/pricing) | 2026-11-17 |
| linkup | `linkup-render-js` | `linkup:linkup-render-js` (unverified) | `renderJs=true` | JavaScript rendered first | $0.005 per request; $5 per 1k at `mode=standard`; with `mode=pro` $0.01, so it adds $0.005 to `linkup-pro` | [docs.linkup.so](https://docs.linkup.so/pages/documentation/development/pricing) | 2026-11-17 |
| browserbase | default | — | markdown | — | $1 per 1k; Fetch API calls past the included 1,000 (Free and Developer $20/mo); $0.50 per 1k past 10,000 on Startup ($99/mo); $4 per 1k with proxies, which web-router never turns on itself | [www.browserbase.com](https://www.browserbase.com/pricing) | 2026-11-08 |
| browserbase | `browserbase-proxies` | `browserbase:browserbase-proxies` (unverified) | `proxies=true` | Fetched through Browserbase's proxies | $4 per 1k; $4 per 1k with proxies against $1 without | [www.browserbase.com](https://www.browserbase.com/pricing) | 2026-11-17 |
| specstream | default | — | markdown (default) or text | — | $0.80 per 1k; per page read; a page that gives no text is not charged | [specstream.ai](https://specstream.ai/agents.md) | 2026-11-19 |
<!-- /prices -->

## Templates (Extract Templates)

Not benchmarked; listed because a run bills on the vendor's account, and
because the `live_data` MCP tool and the docs live data page (docs/live-data.mdx) point here for the figure.

<!-- prices: method=live_data -->
| Backend | What web-router sends | Estimated price | Source | Checked |
|---|---|---|---|---|
| nimble | `extract.templates.run`, one template and its inputs, one page | $3 per 1k; per run, one flat rate for every template; successes only; listing and describing templates are free | [docs.nimbleway.com](https://docs.nimbleway.com/nimble-sdk/admin/pricing) | 2026-10-28 |
| apify | `run-sync-get-dataset-items`, one Actor and its input | set per Actor: free, per dataset item, or per event, plus platform compute units; see each Actor's Store page | [apify.com](https://apify.com/pricing) | 2026-11-09 |
| brightdata | `/datasets/v3/scrape`, one scraper and one input | $1.50 per 1k records pay as you go ($1.30 on Scale), so a run costs per record returned, which is not known before it runs; unknown here so a cost cap never under-reserves | [brightdata.com](https://brightdata.com/pricing/web-scraper) | 2026-11-09 |
| oxylabs | Web API dedicated scrapers, `POST /v1/scrape/{scraper}` (or its async job), `output: ["json"]` | per result, and a run returns one result per page it asks for (`pages`), so it costs per result returned, which is not known before the run. Rates are set by scraper and monthly volume: Amazon $0.25-0.50, Google $0.50-1.35, ChatGPT, Perplexity and Gemini $1.30-1.50 per 1k results. Credits come from a monthly plan, from $15 | [oxylabs.io](https://oxylabs.io/products/scraper-api/web/pricing) | 2026-11-11 |
<!-- /prices -->

**One rate, not per template (checked 2026-10-28).** The template API
publishes no price per template -- not in a listing, not in a description --
because there is none: every run is the same $3.00 per 1k, on both of
Nimble's pricing pages.

## Answer

What one `answer()` costs at list price, checked 2026-11-01. The answer scan
(`answer.py`) shows a backend's own stated cost per answer where its response
carries one; this table is for reading the others.

<!-- prices: method=answer -->
| Backend | Mode | Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|---|---|
| linkup | default | — | `/v1/search`, `outputType=sourcedAnswer`, `depth=fast` | — | $6 per 1k; $5 per 1k at `fast`, plus $0.001 a call for `sourcedAnswer` | [www.linkup.so](https://www.linkup.so/pricing) | 2026-11-01 |
| linkup | `linkup-flash` | `linkup:linkup-flash` (unverified) | `depth=flash` | Lowest latency | $6 per 1k; $0.006 a `sourcedAnswer` at `flash` | [docs.linkup.so](https://docs.linkup.so/pages/documentation/development/pricing) | 2026-11-18 |
| linkup | `linkup-standard` | `linkup:linkup-standard` (unverified) | `depth=standard` | One agentic pass for multi-topic questions | $6 per 1k; $0.006 a `sourcedAnswer` at `standard` | [docs.linkup.so](https://docs.linkup.so/pages/documentation/development/pricing) | 2026-11-18 |
| linkup | `linkup-deep` | `linkup:linkup-deep` (unverified) | `depth=deep` | Several search iterations | $55 per 1k; $0.055 a `sourcedAnswer` at `deep` | [docs.linkup.so](https://docs.linkup.so/pages/documentation/development/pricing) | 2026-11-18 |
| exa | default | — | `/answer`, the query alone | — | $5 per 1k; its response states the cost under `costDollars` ($0.005 an answer in the answer scan) | [exa.ai](https://exa.ai/docs/reference/pricing) | 2026-11-01 |
| exa | `exa-fast` | `exa:exa-fast` (unverified) | `model=exa-fast` | Exa's fast answer model | the pricing page lists `/answer` at $5 per 1k and names no model; its response states the cost under `costDollars` | [exa.ai](https://exa.ai/docs/reference/pricing) | 2026-11-18 |
| exa | `exa-pro` | `exa:exa-pro` (unverified) | `model=exa-pro` | Exa's pro answer model | the pricing page lists `/answer` at $5 per 1k and names no model; its response states the cost under `costDollars` | [exa.ai](https://exa.ai/docs/reference/pricing) | 2026-11-18 |
| exa | `exa-research` | `exa:exa-research` (unverified) | `model=exa-research` | Exa's research answer model | the pricing page lists `/answer` at $5 per 1k and names no model; its response states the cost under `costDollars` | [exa.ai](https://exa.ai/docs/reference/pricing) | 2026-11-18 |
| parallel | default | — | Chat API (beta), `model=lite` | — | not checked here; see the vendor's page | [parallel.ai](https://parallel.ai/pricing) | — |
| you | default | — | `/v1/answer` on `api.you.com` | — | $5 per 1k; its response states no cost | [you.com](https://you.com/docs/additional-resources/changelog/2026/8/5.md) | 2026-11-01 |
| perplexity | default | — | `/v1/responses`, `effort=fast` | — | billed by tokens; tokens plus tools: $0.001 a web search, and the `fast` model's tokens; its response states the total under `usage.cost`: $0.0012 an answer on average over the answer scan of 300 SimpleQA questions on 2026-11-01 | [docs.perplexity.ai](https://docs.perplexity.ai/docs/agent-api/presets.md) | 2026-11-01 |
| perplexity | `perplexity-low` | `perplexity:perplexity-low` (unverified) | `effort=low` | More search and reasoning than `fast` | billed by tokens; tokens plus tools at this effort; no per-call price is published, and its response states the total under `usage.cost` | [docs.perplexity.ai](https://docs.perplexity.ai/docs/agent-api/presets.md) | 2026-11-18 |
| perplexity | `perplexity-medium` | `perplexity:perplexity-medium` (unverified) | `effort=medium` | Deeper than `low` | billed by tokens; tokens plus tools at this effort; no per-call price is published, and its response states the total under `usage.cost` | [docs.perplexity.ai](https://docs.perplexity.ai/docs/agent-api/presets.md) | 2026-11-18 |
| perplexity | `perplexity-high` | `perplexity:perplexity-high` (unverified) | `effort=high` | The deepest one-call answer | billed by tokens; tokens plus tools at this effort; no per-call price is published, and its response states the total under `usage.cost` | [docs.perplexity.ai](https://docs.perplexity.ai/docs/agent-api/presets.md) | 2026-11-18 |
| brave | default | — | `/res/v1/chat/completions`, `model=brave`, streamed with `enable_citations` | — | $4 per 1k; plus tokens ($5 per million); its stream states the cost in a `<usage>` tag: $0.054 for one question on 2026-11-03 (9,887 input tokens), not measured over the scan | [brave.com](https://brave.com/search/api/) | 2026-11-01 |
<!-- /prices -->

## Research

Not benchmarked and not reachable from a search setup; listed because a run bills
by the run (or, for Perplexity, by its tokens and tool calls) rather than by the
request. Each row is the vendor tier web-router's normalized `effort` maps to
(`NIMBLE_RESEARCH_EFFORT`, `PARALLEL_RESEARCH_PROCESSORS`,
`PERPLEXITY_RESEARCH_PRESETS`, `LINKUP_RESEARCH_DEPTHS`); checked 2026-10-07.

<!-- prices: method=research -->
| Backend | Mode | What web-router sends | Estimated price | Source | Checked |
|---|---|---|---|---|---|
| nimble | default | no `effort` (the vendor's default) | no effort set: Nimble's own default effort, which it does not price | [docs.nimbleway.com](https://docs.nimbleway.com/nimble-sdk/web-search-agents/efforts.md) | 2026-10-07 |
| nimble | `low` | Web Search Agent `effort=low`, 10 to 30 s | $0.025 per run | [docs.nimbleway.com](https://docs.nimbleway.com/nimble-sdk/web-search-agents/efforts.md) | 2026-10-07 |
| nimble | `medium` | `medium`, 1 to 3 minutes | $0.10 per run | [docs.nimbleway.com](https://docs.nimbleway.com/nimble-sdk/web-search-agents/efforts.md) | 2026-10-07 |
| nimble | `high` | `high`, 5 to 15 minutes | $0.50 per run | [docs.nimbleway.com](https://docs.nimbleway.com/nimble-sdk/web-search-agents/efforts.md) | 2026-10-07 |
| nimble | `max` | `x-high` | sends x-high, Nimble's deepest open tier; its $2.00 per run is not recorded yet | [docs.nimbleway.com](https://docs.nimbleway.com/nimble-sdk/web-search-agents/efforts.md) | 2026-10-07 |
| parallel | `low` | processor `lite` | $0.005 per run | [docs.parallel.ai](https://docs.parallel.ai/getting-started/pricing) | 2026-10-07 |
| parallel | `medium` (default) | `core` | $0.025 per run | [docs.parallel.ai](https://docs.parallel.ai/getting-started/pricing) | 2026-10-07 |
| parallel | `high` | `pro` | $0.10 per run | [docs.parallel.ai](https://docs.parallel.ai/getting-started/pricing) | 2026-10-07 |
| parallel | `max` | `ultra` | $0.30 per run | [docs.parallel.ai](https://docs.parallel.ai/getting-started/pricing) | 2026-10-07 |
| perplexity | `low` | preset `low`: `openai/gpt-6-luna`, up to 5 steps | billed by tokens; $0.10 / $0.50 per 1M input / output tokens, plus tools | [docs.perplexity.ai](https://docs.perplexity.ai/docs/agent-api/presets.md) | 2026-10-07 |
| perplexity | `medium` (default) | preset `medium`: `gpt-6-luna`, up to 15 steps | billed by tokens; $0.10 / $0.50 per 1M input / output tokens, plus tools | [docs.perplexity.ai](https://docs.perplexity.ai/docs/agent-api/presets.md) | 2026-10-07 |
| perplexity | `high` | preset `high`: `openai/gpt-6-sol`, up to 15 steps | billed by tokens; $2.00 / $10.00 per 1M input / output tokens, plus tools | [docs.perplexity.ai](https://docs.perplexity.ai/docs/agent-api/presets.md) | 2026-10-07 |
| perplexity | `max` | preset `xhigh`: `anthropic/claude-opus-5-5`, up to 100 steps | billed by tokens; $4.00 / $20.00 per 1M input / output tokens, plus tools | [docs.perplexity.ai](https://docs.perplexity.ai/docs/agent-api/presets.md) | 2026-10-07 |
| linkup | `low` | `reasoningDepth=S`, 2 to 5 minutes | $0.25 per run | [docs.linkup.so](https://docs.linkup.so/pages/documentation/endpoints/research/overview.md) | 2026-10-07 |
| linkup | `medium` | `M`, 3 to 7 minutes | $0.50 per run | [docs.linkup.so](https://docs.linkup.so/pages/documentation/endpoints/research/overview.md) | 2026-10-07 |
| linkup | `high` (default) | `L`, 5 to 10 minutes | $1.50 per run; Linkup's own default | [docs.linkup.so](https://docs.linkup.so/pages/documentation/endpoints/research/overview.md) | 2026-10-07 |
| linkup | `max` | `XL`, 10 to 20 minutes | $2.50 per run | [docs.linkup.so](https://docs.linkup.so/pages/documentation/endpoints/research/overview.md) | 2026-10-07 |
| you | default | — | no price recorded here yet | — | — |
<!-- /prices -->

**Perplexity has no per-run price.** A run costs its tokens plus each tool call:
`web_search` $0.001 (fast, low and medium presets) or $0.0025 (standard),
`fetch_url` $0.0005, `finance_search` $0.005, a `sandbox` session $0.03 per
20 minutes. The presets are unversioned and may switch models; the pricing
page's calculator still names the older `gpt-5.6-luna` and `gpt-5.6-sol`
($0.20 / $1.20 and $4 / $20), and its per-run figures are "median values … NOT
billed values". Past 272K input tokens the OpenAI models' input price doubles and
the output price rises by half: `gpt-6-luna` $0.20 / $0.75, `gpt-6-sol` $4 / $15
per 1M tokens.

Tiers web-router does not map, reachable only through `provider_options`:
Parallel `base` ($0.01) and `ultra2x` / `4x` / `8x` ($0.60 / $1.20 / $2.40). Parallel bills only completed runs. The one live Linkup
run, at `effort=low`, took about 105 seconds.

## Browser sessions

Billed by the browser-hour while a session is open, plus proxy traffic. The
`BROWSER_TIMEOUT` default (10 minutes) bounds a session nobody released.

<!-- prices: method=open_browser -->
| Backend | Estimated price | Source | Checked |
|---|---|---|---|
| steel | $0.10 an hour; Launch: no fee, $30 one-time credit; $0.08 an hour on Scale ($250/mo); proxy $10 / $6 per GB; CAPTCHA solves $3 / $1 per 1k; stealth is Enterprise-only (web-router does not map it for Steel) | [docs.steel.dev](https://docs.steel.dev/overview/pricinglimits) | 2026-10-07 |
| browserbase | $0.12 an hour; past the included hours (Developer $20/mo, 100 h); $0.10 an hour (Startup $99/mo, 500 h); proxy $12 / $10 per GB past 1 / 5 GB included, none on Free; advanced stealth (web-router's `stealth="high"`) only on Scale, custom price | [www.browserbase.com](https://www.browserbase.com/pricing) | 2026-10-07 |
| hyperbrowser | $0.10 an hour; 100 credits an hour, 1 credit = $0.001; proxy $10 per GB; plan tiers and stealth prices not published on a page that could be read; `stealth="high"` maps to ultra stealth | [hyperbrowser.ai](https://hyperbrowser.ai/docs/pricing) | 2026-10-07 |
| anchor | $0.05 an hour; plus $0.01 per browser created; proxy $8 per GB (Anchor's proxy), $0.20 per GB own or none; plans Free ($5 credit) to Growth ($2,000/mo); stealth and captcha are plan features. The pricing page's worked example shows $0.09 an hour and no creation fee: recheck before quoting | [docs.anchorbrowser.io](https://docs.anchorbrowser.io/pricing) | 2026-10-07 |
| kernel | no price recorded here yet | — | — |
| local | free; the caller's own Chromium | — | — |
<!-- /prices -->

## Browse tasks (hosted browser agents)

<!-- prices: method=browse -->
| Backend | Mode | Setup | What web-router sends | What it returns | Estimated price | Source | Checked |
|---|---|---|---|---|---|---|---|
| browser-use | default | — | legacy v2 `/api/v2/tasks` | — | $0.01 per run; per task, plus per agent step by model: `browser-use-2.0` (the default) $0.006, `gemini-flash-lite-latest` $0.005, `gemini-flash-latest` $0.0075, `o3` $0.03, Claude Sonnet $0.05; browser time $0.02 per hour and residential proxy $5 per GB are billed on top for Cloud | [docs.browser-use.com](https://docs.browser-use.com/cloud/legacy/agent) | 2026-10-07 |
| browser-use | `browser-use-gpt-5.6-luna` | `browser-use:browser-use-gpt-5.6-luna` (unverified) | `llm=gpt-5.6-luna` | The agent on `gpt-5.6-luna` | $0.004 per agent step on the v2 API; the steps a task takes are not known before it runs, and browser time and proxies are billed on top | [docs.browser-use.com](https://docs.browser-use.com/cloud/legacy/agent) | 2026-11-18 |
| browser-use | `browser-use-gemini-flash-lite-latest` | `browser-use:browser-use-gemini-flash-lite-latest` (unverified) | `llm=gemini-flash-lite-latest` | The agent on `gemini-flash-lite-latest` | $0.005 per agent step on the v2 API; the steps a task takes are not known before it runs, and browser time and proxies are billed on top | [docs.browser-use.com](https://docs.browser-use.com/cloud/legacy/agent) | 2026-11-18 |
| browser-use | `browser-use-gemini-flash-latest` | `browser-use:browser-use-gemini-flash-latest` (unverified) | `llm=gemini-flash-latest` | The agent on `gemini-flash-latest` | $0.0075 per agent step on the v2 API; the steps a task takes are not known before it runs, and browser time and proxies are billed on top | [docs.browser-use.com](https://docs.browser-use.com/cloud/legacy/agent) | 2026-11-18 |
| browser-use | `browser-use-gemini-3-flash-preview` | `browser-use:browser-use-gemini-3-flash-preview` (unverified) | `llm=gemini-3-flash-preview` | The agent on `gemini-3-flash-preview` | $0.01 per agent step on the v2 API; the steps a task takes are not known before it runs, and browser time and proxies are billed on top | [docs.browser-use.com](https://docs.browser-use.com/cloud/legacy/agent) | 2026-11-18 |
| browser-use | `browser-use-o3` | `browser-use:browser-use-o3` (unverified) | `llm=o3` | The agent on `o3` | $0.03 per agent step on the v2 API; the steps a task takes are not known before it runs, and browser time and proxies are billed on top | [docs.browser-use.com](https://docs.browser-use.com/cloud/legacy/agent) | 2026-11-18 |
| browser-use | `browser-use-claude-sonnet-5` | `browser-use:browser-use-claude-sonnet-5` (unverified) | `llm=claude-sonnet-5` | The agent on `claude-sonnet-5` | $0.03 per agent step on the v2 API; the steps a task takes are not known before it runs, and browser time and proxies are billed on top | [docs.browser-use.com](https://docs.browser-use.com/cloud/legacy/agent) | 2026-11-18 |
| browser-use | `browser-use-claude-sonnet-4-5-20250929` | `browser-use:browser-use-claude-sonnet-4-5-20250929` (unverified) | `llm=claude-sonnet-4-5-20250929` | The agent on `claude-sonnet-4-5-20250929` | $0.05 per agent step on the v2 API; the steps a task takes are not known before it runs, and browser time and proxies are billed on top | [docs.browser-use.com](https://docs.browser-use.com/cloud/legacy/agent) | 2026-11-18 |
| browser-use | `browser-use-claude-opus-4-8` | `browser-use:browser-use-claude-opus-4-8` (unverified) | `llm=claude-opus-4-8` | The agent on `claude-opus-4-8` | $0.10 per agent step on the v2 API; the steps a task takes are not known before it runs, and browser time and proxies are billed on top | [docs.browser-use.com](https://docs.browser-use.com/cloud/legacy/agent) | 2026-11-18 |
| skyvern | default | — | `/v1/run/tasks` | — | credits by plan: 5,000 free once; Hobby $29/mo for 30k; Pro $149/mo for 150k (residential proxy, advanced captcha). No dollar price per action or overage published, and the pages disagree on what a credit buys | [www.skyvern.com](https://www.skyvern.com/pricing) | 2026-10-07 |
| skyvern | `skyvern-2.0` | `skyvern:skyvern-2.0` (unverified) | `engine=skyvern-2.0` | Skyvern's V2 agent | credits by plan (Hobby $29/mo for 30k, Pro $149/mo for 150k); no dollar price per engine, step or run is published | [www.skyvern.com](https://www.skyvern.com/pricing) | 2026-11-18 |
| skyvern | `skyvern-3.0` | `skyvern:skyvern-3.0` (unverified) | `engine=skyvern-3.0` | Skyvern's V3 agent | credits by plan (Hobby $29/mo for 30k, Pro $149/mo for 150k); no dollar price per engine, step or run is published | [www.skyvern.com](https://www.skyvern.com/pricing) | 2026-11-18 |
| skyvern | `skyvern-openai-cua` | `skyvern:skyvern-openai-cua` (unverified) | `engine=openai-cua` | OpenAI's computer-use model | credits by plan (Hobby $29/mo for 30k, Pro $149/mo for 150k); no dollar price per engine, step or run is published | [www.skyvern.com](https://www.skyvern.com/pricing) | 2026-11-18 |
| skyvern | `skyvern-anthropic-cua` | `skyvern:skyvern-anthropic-cua` (unverified) | `engine=anthropic-cua` | Anthropic's computer-use model | credits by plan (Hobby $29/mo for 30k, Pro $149/mo for 150k); no dollar price per engine, step or run is published | [www.skyvern.com](https://www.skyvern.com/pricing) | 2026-11-18 |
| skyvern | `skyvern-ui-tars` | `skyvern:skyvern-ui-tars` (unverified) | `engine=ui-tars` | The UI-TARS model | credits by plan (Hobby $29/mo for 30k, Pro $149/mo for 150k); no dollar price per engine, step or run is published | [www.skyvern.com](https://www.skyvern.com/pricing) | 2026-11-18 |
| browserbase | default | — | Agents API `/v1/agents/runs` | — | 3 / 15 / 50 runs included (Free / Developer / Startup), custom on Scale; no price per run past those, and model tokens are billed at "market price" with no figure | [www.browserbase.com](https://www.browserbase.com/pricing) | 2026-10-07 |
<!-- /prices -->

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
