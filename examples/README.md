# Examples

Runnable scripts, roughly in the order worth reading them.

| Script | Shows | Needs a key? |
| --- | --- | --- |
| [`basic_search.py`](basic_search.py) | The whole API: search, read normalized results | Nimble |
| [`extract.py`](extract.py) | `extract()`: page contents, formats, partial failure | no |
| [`research.py`](research.py) | `research()`: prose, a schema, enrichment, and a job you can put down | any research key |
| [`browser_session.py`](browser_session.py) | `open_browser()` on `local`: goto, snapshot, click, type, screenshot, read — sync and async | no |
| [`browser_vendor.py`](browser_vendor.py) | The same session through `auto` on a cloud vendor, and your own Playwright over `cdp_url` | any browser key |
| [`browse.py`](browse.py) | `browse()`: a task handed to a hosted browser agent, waited on or submitted | any browse key |
| [`main.py`](main.py) | Guided tour of search and extract in one file | no |
| [`routing.py`](routing.py) | Defaults, per-call overrides, `provider_config`, `provider_options` | no |
| [`error_handling.py`](error_handling.py) | Every exception and what to do about each | no |
| [`search_depth.py`](search_depth.py) | `lite` / `fast` / `deep` measured across backends | no |
| [`custom_provider.py`](custom_provider.py) | Adding your own backend, against Wikipedia's open API | no |
| [`async_search.py`](async_search.py) | `AsyncWebRouter`, several queries at once | Nimble |
| [`compare_providers.py`](compare_providers.py) | `benchmark()`: one query across every usable backend, with latency and result counts | no |
| [`evaluate_accuracy.py`](evaluate_accuracy.py) | `evaluate()`: SimpleQA or your CSV through every usable backend, graded by an LLM judge — bills | `OPENAI_API_KEY` + any search key |

## Running them

Most need no credentials at all: with no key, the default `auto` provider falls
back to the anonymous tiers, and the examples that fan out use
`configured_providers()` — or `configured_extract_providers()` — to route only
to backends this environment can use.

`research.py`, `browse.py` and `browser_vendor.py` are the exceptions, and not
by oversight: every agentic backend bills for the run and every cloud browser
for the time it is open, so there is no anonymous tier for them to fall back on.
With no key each names the variables that would fix it and exits.
`browser_session.py` needs no key but does need the `browser` extra and a
Chrome or Chromium on the machine — `local` launches it for the session and
stops it on the way out. `evaluate_accuracy.py` needs the `evals` extra and
`OPENAI_API_KEY` for the judge, and bills on both sides — a search per backend
per question, and two judge calls per answer — so it starts at twenty questions.

```bash
make setup   # in your webrouter clone: every extra
uv run python examples/main.py
```

Examples read the environment directly — `.env` is loaded for tests only — so
export your keys, or run them through the file:

```bash
uv run --env-file .env python examples/main.py
```

Anything that needs a key skips or reports itself rather than failing, so a
partial set of credentials still gives you a useful run.
