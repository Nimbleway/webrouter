# Examples

Runnable scripts, roughly in the order worth reading them.

| Script | Shows | Needs a key? |
| --- | --- | --- |
| [`basic_search.py`](basic_search.py) | The whole API: search, read normalized results | Nimble |
| [`extract.py`](extract.py) | `extract()`: page contents, formats, partial failure | no |
| [`extract_max.py`](extract_max.py) | `max`: every extract backend at once, the first document wins, and a `hedge` that keeps most calls to one fetch | no |
| [`live_data.py`](live_data.py) | `live_data_sources()`, `live_data_source()`, `live_data()`: the catalogue, a source's inputs, and the records it fetches | Nimble |
| [`answer.py`](answer.py) | `answer()`: a backend's written answer and its sources in one call, the markers that tie them, a refused option, and what an unnamed call does | any answer key |
| [`research.py`](research.py) | `research()`: prose, a schema, enrichment, and a job you can put down | any research key |
| [`browser_session.py`](browser_session.py) | `open_browser()` on `local`: goto, snapshot, click, type, screenshot, read — sync and async | no |
| [`browser_vendor.py`](browser_vendor.py) | The same session through `auto` on a cloud vendor, and your own Playwright over `cdp_url` | any browser key |
| [`browse.py`](browse.py) | `browse()`: a task handed to a hosted browser agent, waited on or submitted | any browse key |
| [`main.py`](main.py) | Guided tour of search and extract in one file | no |
| [`routing.py`](routing.py) | Defaults, per-call overrides, `provider_config`, `provider_options` | no |
| [`router_rules.py`](router_rules.py) | Your own router in a few lines: a URL extracts, "compare", "report" or "analyze" researches, anything else has no opinion | no |
| [`router_classifier.py`](router_classifier.py) | A scikit-learn pipeline, saved and loaded with joblib, ranking methods by `predict_proba`; `timeout_s = 0.05` | no, but scikit-learn |
| [`router_decider.py`](router_decider.py) | Your own decider in a few lines: `DeciderRouter` asks it one question and checks its ranking; registered by name with `register_router` | no |
| [`router_llm.py`](router_llm.py) | The built-in `router="llm"` on a preset (`LLM_ROUTER_PRESET`, `openai` unless set): a chat model ranks the candidates, held to their ids; 3 s — sends the prompt to that server, bills | `LLM_ROUTER_MODEL` + the preset's key, such as `OPENAI_API_KEY` |
| [`router_remote_server.py`](router_remote_server.py) | A router service in the standard library for `router="remote"`: the JSON wire format, schema 1, around the built-in rules router; `--demo` plans through it | no |
| [`router_jev.py`](router_jev.py) | An async router on TypeSafe's Jev written out by hand, ranking by its probability per candidate; `timeout_s = 1.0` — sends the prompt to TypeSafe, bills. `router="systemone"` is the same built in | `TYPESAFE_API_KEY` |
| [`router_openai_decisions.py`](router_openai_decisions.py) | OpenAI's Decisions API as a decider on `DeciderRouter`: one `choice` question over the candidates, ranked by its probability per option; 3 s — sends the prompt to OpenAI, bills | `OPENAI_API_KEY` |
| [`error_handling.py`](error_handling.py) | Every exception and what to do about each | no |
| [`search_depth.py`](search_depth.py) | `lite` / `fast` / `deep` measured across backends | no |
| [`custom_provider.py`](custom_provider.py) | Adding your own backend, against Wikipedia's open API | no |
| [`async_search.py`](async_search.py) | `AsyncWebRouter`, several queries at once | Nimble |
| [`compare_providers.py`](compare_providers.py) | `benchmark()`: one query across every usable backend, with latency and result counts | no |
| [`compare_extract_providers.py`](compare_extract_providers.py) | `benchmark_extract()`: one URL across every usable extract backend, with latency and what came back | no |
| [`evaluate_accuracy.py`](evaluate_accuracy.py) | `evaluate()`: SimpleQA or your CSV through every usable backend, graded by an LLM judge — bills | `OPENAI_API_KEY` + any search key |

## Running them

Most need no credentials at all: with no key, the default `auto` provider falls
back to the anonymous tiers, and the examples that fan out use
`configured_providers()` — or `configured_extract_providers()` — to route only
to backends this environment can use.

`answer.py`, `research.py`, `browse.py`, `browser_vendor.py` and `live_data.py` are
the exceptions, and not by oversight: every agentic backend bills for the run,
every cloud browser for the time it is open, and a live data run fetches on the
backend's account, so there is no anonymous tier for them to fall back on.
With no key each names the variables that would fix it and exits.
`router_llm.py`, `router_jev.py` and `router_openai_decisions.py` are exceptions too: each plan sends the
prompt to a model on another server, which bills for it unless it is your own. With no
key each says what to set and exits. `router_classifier.py` needs no key but
does need scikit-learn, which web-router does not depend on: run it with
`uv run --with scikit-learn python examples/router_classifier.py`. The
routers only `plan()`, which runs no search, so `router_rules.py`,
`router_decider.py` and `router_classifier.py` call nothing at all, and `router_remote_server.py
--demo` calls only the server it starts on this machine.
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
