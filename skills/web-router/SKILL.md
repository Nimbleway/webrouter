---
name: web-router
description: >
  Search, read and research the web through web-router's MCP tools, choosing
  the cheapest method that answers. Use when a task needs live web data —
  finding pages, reading a known URL, multi-step research, or driving a
  browser — and the web-router MCP server is connected. Covers method
  selection, cost awareness, job polling, and browser-session hygiene.
---

# web-router

One typed interface over many web backends, six methods. The tools describe
*your* install: their `provider` enums list only backends this machine holds
keys for, and `search`/`extract` work with no key at all. Read the
`web-router://capabilities` resource once to learn what is configured before
planning around a paid method.

## Choose the cheapest method that answers

Work up this ladder and stop at the first rung that serves the task:

1. **`search`** — find pages. Ranked links with page text, about a second,
   free on the keyless tier. Most questions end here.
2. **`extract`** — read pages you already have URLs for, about a second.
   Prefer `search` + `extract` over anything below: two cheap calls that you
   compose beat one expensive call you wait on.
   **`live_data`** sits beside it: when `live_data_sources` lists a vendor's
   ready-made scraper for the site and item at hand (pass `query` to search
   the catalogue), one run fetches the site now and returns its fields as
   records, one billed run. All three live data tools need `provider` named.
   Read `live_data_source` first — its `input_schema` says exactly what
   `inputs` to pass, a URL for some sources and an id or a search term for
   others.
3. **`research`** — an agentic run that searches, reads and cross-checks over
   many turns. **Minutes, and it bills.** Use only when the question genuinely
   needs synthesis across many sources that iterative `search`/`extract`
   cannot reach in a few calls.
4. **`browse`** — a hosted browser agent drives a real browser through a task.
   **Minutes, and it bills.** Use when the answer is behind interaction —
   forms, pagination, dynamic pages — and you can state the task in one
   sentence.
5. **`browser_*`** — you drive the browser yourself. `local` is free; a cloud
   session **bills per minute until closed**. Use when the interaction needs
   your judgment step by step rather than a delegated task.

## Cost rules

- Never start a second `research` or `browse` run for a question whose first
  run is still going: `research`/`browse` return a handle after ~25 seconds —
  poll `research_status`/`browse_status` (free) instead of calling the method
  again, which starts and bills a second run.
- `fetch_raw` is free and instant: every result carries a `handle`, and the
  backend's full payload is already held for it. Reach for `fetch_raw` before
  re-calling a tool for more detail.
- Close every browser session with `browser_close` the moment you are done.
  Prefer `provider="local"` unless the task needs stealth, a proxy country or
  CAPTCHA solving, which only billed cloud vendors serve.
- Leave `provider` unset (`auto`) unless you have a reason: it prefers the
  backends configured here and falls back to the keyless tier.
- `extract` with `provider="max"` reads the page through every configured
  backend at once and returns the first answer -- the fastest read possible,
  at one fetch per backend. Use it only when the user has asked for speed
  over cost, never by default.

## What the tools will and will not do

- A parameter a backend cannot serve is refused by name, never silently
  dropped — on a refusal, drop the option or pick another provider; do not
  retry the same call unchanged.
- Results are shaped to fit a context window and say when they were cut; the
  handle gets you the rest. Do not treat a truncation note as missing data.
- Browser tools open only `http:`/`https:` pages and `about:blank`; refs from
  `browser_snapshot` go stale on the next snapshot or navigation — take a
  fresh snapshot after the page changes rather than retrying a stale ref.
- The server never shows you a CDP URL, live-view URL or credential, and
  redacts them from every browse and browser output. Do not ask for them or
  try to reconstruct them.
