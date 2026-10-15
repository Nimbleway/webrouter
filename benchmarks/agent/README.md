# Agent benchmark: the same agent, on its platform's built-in web search and on web-router

This is the comparison the router has to win. An agent on the web search its
platform already gives it, against the same agent on web-router's tools and
the routing guide that says when to reach for which.

Every arm is the same agent, with the same model and the same task prompt,
answering [FRAMES](https://huggingface.co/datasets/google/frames-benchmark)
questions — multi-hop, each answer put together from several Wikipedia
articles. Only the web tools change, and for web-router, the guide.
`--platform` picks whose agent: a Claude agent on **Managed Agents** (the
default), or an OpenAI model on the **Responses API**.

```bash
uv run --all-extras --group agent-bench python benchmarks/agent/run.py --limit 20
uv run --all-extras --group agent-bench python benchmarks/agent/run.py --limit 20 --platform openai
```

## Both platforms in one run

`--platform claude,openai` (or `both`) runs a Claude agent and an OpenAI agent
on the same questions, graded by one judge, into one report. A `web-router:`
arm runs on each platform and is named for it — `claude/web-router:nimble` and
`openai/web-router:nimble` — and each arm is compared with its own platform's
native search, never with the other platform's, because the two run different
models and count tokens differently. `claude/` or `openai/` in front of an arm
runs it on that platform alone.

```bash
uv run --all-extras --group agent-bench python benchmarks/agent/run.py --platform both \
  --arms claude-native,openai-native,web-router:perplexity,web-router:nimble,web-router:linkup,web-router:brave \
  --limit 30 --seed 1
```

- **One judge for every arm.** A run of both defaults to `--judge openai`
  (`gpt-4o`, the judge `benchmarks/providers/` uses), so it grades the OpenAI
  arms with their own family; the report says so. `--judge anthropic` flips it.
- **`--model` per platform:** `--model claude=claude-opus-5,openai=gpt-5`.
- **`--concurrency` applies per platform**, so each runs that many at once.

Runs made separately merge the same way, when their questions, repeats, search
size and judge match — `report.py` refuses them otherwise:

```bash
uv run python benchmarks/agent/report.py --combine <claude-run> <openai-run> --out benchmarks/agent/results/<name>
```

`--arms` keeps only the arms named, by their combined names
(`claude-native,claude/web-router:perplexity,claude/mcp:perplexity,...`), for
a report on one vendor from runs that hold more. It works only with
`--combine`; on a single run's folder it is refused.

Keys come from the repo's `.env` (or the environment), plus each web-router
arm's backend:

| Platform | The agents | The judge |
| --- | --- | --- |
| `claude` | `ANTHROPIC_API_KEY` | `OPENAI_API_KEY` (`OpenAIJudge`, `gpt-4o`) |
| `openai` | `OPENAI_API_KEY` | `ANTHROPIC_API_KEY` (`AnthropicJudge`, `claude-sonnet-5-5`) |

By default the judge is from the other family, so no arm is graded by its
own. `--judge openai|anthropic` picks one yourself, and `--judge-model` its
model, for example to grade a Claude run with Claude or to grade both
platforms with the same judge:

```bash
uv run --all-extras --group agent-bench python benchmarks/agent/run.py --limit 20 --judge anthropic
```

A judge from the agents' own family is allowed, and the report's setup says so
in bold, since it is not independent of what it grades. Accuracy is
comparable only between runs graded by the same judge and model, which the
report names. With the defaults, a Claude run and an OpenAI run are graded by
different judges, so compare arms within one run, or pass the same `--judge`
to both.

## Arms

| Arm | Web tools | What it shows |
| --- | --- | --- |
| `claude-native` | Claude's built-in `web_search` and `web_fetch`, run by Anthropic | The baseline, on `--platform claude` |
| `openai-native` | OpenAI's built-in `web_search`, run by OpenAI | The baseline, on `--platform openai` |
| `web-router:<provider>` | `web_router_search` + `web_router_extract` on `<provider>`, with `routing_guide()` in the system prompt | The router: tools and judgment together |
| `web-router:<provider>:no-guide` | the same tools, no guide | What the tools alone do — and, against the guided arm, what the guide adds |
| `mcp:<vendor>` | the vendor's own hosted MCP server, its search tool and its page-read tool where it has one, called by the platform | The control: what web-router adds over connecting the vendor directly |
| `mcp:all` | five vendors' hosted servers at once (`MCP_SETS`), the agent choosing among them | The direct side of the multi-vendor comparison |
| `mcp:<vendor or set>:shuffled` | the same servers, listed in a seeded order of their own for each question, recorded on the row as `mcp_order` | The direct side with no vendor always listed first, which steers an agent's pick |
| `direct:<vendor>` | the vendor's own REST API as two plain tools, search and page read, its JSON returned as it comes back, no routing guide, run here | The control for a vendor with no hosted MCP server: what web-router adds over calling the vendor yourself |
| `no-search` | no web tool at all: the same agent and prompt | The baseline that shows how much search does the work |
| `web-router:auto-shipped` / `web-router:auto-tuned` | web-router's tools on `auto` over the same five vendors, in the order `auto` ships with / the order the benchmarks favour (`WEB_ROUTER_PRESETS`) | The web-router side of it: as a user gets it, and configured |

`<provider>` is any search backend, or `auto`. The tools expose search and
extract today; research and browse join once the routing guide can say when
each is worth its bill.

`<vendor>` is a key of `MCP_SERVERS` in `run.py`: `perplexity`, `exa`,
`parallel`, `tavily`, `nimble` and `keenable`, each checked live with its key as a bearer
token. `keenable` is not in `mcp:all`, which stays the five vendors earlier runs compared. Each is given its search tool and its page-read tool where it has one
(Perplexity's server has none); research, crawl and agent tools are left out,
as web-router's research and browse are. `mcp:perplexity` against
`web-router:perplexity` is one backend behind two sets of tools. An `mcp:` arm is the vendor as shipped: its server decides what
a search returns (Perplexity's and Exa's 10 results by default, Nimble's 3,
Tavily's 5) and the model picks how many results to ask for, where
web-router sends `--max-results` and cuts the text to a budget; and the
platform makes the calls, so its latency includes the
platform's round trips. The vendor's key goes to the platform: on Claude a
static-bearer credential in a Managed Agents vault, created once and set from
the environment on every run; on OpenAI an `Authorization` header on each
request. Neither the key nor anything derived from it is written to
`.managed-agents.json` or the results.

`<vendor>` in a `direct:` arm is `webiq`, the one vendor with no hosted MCP
server to attach; its endpoints and fields come from web-router's own WebIQ
provider, so the two arms call the same API. The request is fixed to
what the vendor's docs show for a model to read — WebIQ's `passage` search and
markdown Browse at its own 10,000-character default — and recorded under what
each backend was sent; the model picks only the query or the URL. The calls run
on this machine, so the vendor's key goes only to the vendor, as web-router's
does, and never to the platform or the results; an error body the vendor sends
back reaches the model with the key masked. A refused read of an answer-key page
is counted under `withheld`, not as a tool error.

## How it runs

On Claude:

- **Agents are created once.** The first run creates an environment and one
  agent per arm, and records their ids in `.managed-agents.json` (gitignored).
  Later runs reuse them; an arm whose prompt, tools or model changed updates
  its agent to a new version rather than creating another. Nothing is archived
  but sessions.
- **One session per question.** Each is capped by `--session-budget` (cents of
  Anthropic list cost, 200 by default) and `--timeout` (600 seconds), and
  archived when it ends.
- **web-router's tools run here**, on this machine's keys, as custom tools: the
  session pauses on each call, the script runs it and sends the result back.
  Claude's built-in tools run on Anthropic's side, and so does an `mcp:` arm's
  server: the environment allows MCP servers (`allow_mcp_servers`), which
  opens only the ones an agent names.

On OpenAI:

- **Nothing is created.** Each question is one chain of Responses API
  requests, linked by `previous_response_id`; the script runs web-router's
  function calls between them. OpenAI's built-in `web_search` runs on OpenAI's
  side, inside a single request, and so does an `mcp:` arm's server — so
  `--max-turns` does not bound it; `--timeout` does.
- **Each run is capped by `--max-turns`** (30 requests) and `--timeout`.
  OpenAI has no per-run cost cap, so the turn limit is what stops a loop.
- **No cost column.** OpenAI reports tokens, not a cost, and the report never
  estimates one: price its token and search counts at OpenAI's rates. Tool
  chars are `n/a` for `openai-native`, because OpenAI keeps what its search
  read on its side.

## What it reports

Each run gets its own folder under `benchmarks/agent/results/` (or `--out`):

| File | What's in it |
| --- | --- |
| `report.md` | The whole result, explained: setup, headline, winners by axis, the results table with every column defined, each arm against the baseline, what the guide adds, question-by-question wins and losses, accuracy by reasoning type, which backend served each call, every question's grade per arm, failed runs, and how to read it |
| `summary.json` | The same numbers, for charts and slides |
| `page.html` | A page for stakeholders from `summary.json`: the headline per platform, winners by axis with their verdicts, charts and the caveats — ready to publish or share |
| `rows.jsonl` | One line per question, arm and repeat — the answer, the grade, the session id and everything counted — written as each run finishes |
| `run.json` | The setup the run used |

**Winners by axis** names the best arm on each axis — accuracy, failure rate,
median input tokens, median tool chars, tool calls, p50 and p95 latency, and
Anthropic's list cost — within each platform, with its margin over the
runner-up and over native search. Each margin carries a verdict from a paired
bootstrap over the runs both arms served: **clear** when its 95% interval
leaves out zero, **within noise** when it does not, and none on fewer than five
paired runs. An arm with no figure on an axis — OpenAI's native tool chars, any
OpenAI cost — is left out of it rather than ranked on a zero.

**Arms whose tools failed.** A web-router arm in which no search succeeded,
or at least half of its tool calls errored, measured an outage — an expired
key, a revoked endpoint — not its backend. The report flags it at the top,
keeps it in the tables, and leaves it out of the headline and winners by axis.
`run.py` prints a warning as soon as an arm has five runs in a row with tool
errors and no search, so a broken key shows in minutes rather than at the end.

`python benchmarks/agent/report.py <folder>` rebuilds `report.md` and
`summary.json` from `rows.jsonl` without rerunning anything.

The columns:

- **accuracy** — correct / served, graded by `evaluate()`'s judge
  (`openai:gpt-4o`, the SimpleQA grader) — the same judge for every arm, and
  not a Claude model, so no arm is graded by its own family. Shown with its 95%
  Wilson interval.
- **acc. when attempted** — correct / (correct + incorrect).
- **failure rate** — runs where the session errored, stopped on its budget or
  retries, ran past `--timeout`, or gave no answer; left out of accuracy.
- **tool calls, model calls** — means per served run, counted from the
  session's events.
- **input tokens** — everything the model read over the run (uncached, cache
  writes and cache reads), from each model request's usage; median first.
- **tool chars** — characters the tools returned into the context; median.
- **Claude web searches** — the built-in tool's searches, from the session's
  usage. Anthropic bills these per search.
- **Anthropic list cost** — the session's cost at public list rates, as
  Anthropic reports it: tokens, Claude's web searches and runtime. **It does
  not include a web-router backend's charges**, which no backend here states,
  so it is the Anthropic side of the bill, not all of it.
- **p50 / p95 latency** — wall-clock seconds per run.

Each row also records `calls`: every backend call that was served, as
`<vendor>:search` or `<vendor>:read` (one per URL read), so a run can be priced
at each vendor's own rates. For an `mcp:` arm a read is one per URL requested,
since the platform does not report which pages came back. The report itself
prices nothing.

The only judgment in the table is accuracy; everything else is measured or
reported by Anthropic.

## Reading the numbers

- Agents are noisy. Twenty questions tell you whether something is badly
  wrong, not which of two close arms is better — use `--limit 100` or more and
  `--repeats 3` before drawing a line between arms a few points apart.
- Against the baseline a guided arm changes two things, the tools and the
  guide. The `:no-guide` arm splits them.
- It bills: every run is a Managed Agents session with several model calls,
  plus a search per tool call, plus one judge call. Start with `--limit 20`.

## Options

`--platform` is `claude` (the default), `openai`, or both. `--model` is the model
every arm's agent runs on: `claude-opus-5` or `gpt-5` by default. See `--help`
for the rest: `--seed`, `--repeats`, `--max-results`, `--concurrency`,
`--timeout`, `--session-budget` (Claude), `--max-turns` (OpenAI), `--judge` and
`--judge-model`, `--dataset` (a local copy of the question set's file) and `--out`.

`--questions` picks the question set: `frames` (the default);
`simpleqa-verified`, Google DeepMind's 1,000 single-fact questions pinned to one
commit of the dataset, which frontier models get wrong without search, so a
`no-search` arm lands well below the search arms; `sealqa-0` or `sealqa-hard`,
SealQA's search-adversarial questions; or `csv`, your own file
from `--dataset`, its columns named by `--question-column` and
`--answer-column`. On SealQA a run keeps the set's answer-key pages — its
dataset, paper and their copies, the pages `evaluate()` keeps from its judge —
from every arm whose tools run here: a `web-router:` or `direct:` search drops
them and a read of one fails, counted on the row as `withheld`. A native or
`mcp:` arm's tools run on the platform, which shows the agent a page before the
run sees it, so those are counted as `answer_key_seen` instead, and the report's
*Answer-key pages* section names both. `--exclude <run folders>`
samples only questions those runs did not ask, from either set. Runs combine
only on the same set.

`--outage <vendor>:<kind>` makes one vendor fail on purpose, on both sides of
the run, to measure what fallback is worth. The vendor is `perplexity`, `exa`
or `parallel`, which web-router reaches over plain httpx; the kind is
`rate-limit`, `server-error`, `revoked-key` or `timeout`.

- **web-router's side:** the vendor's provider gets a transport that answers
  every call with that failure, here, before anything reaches the vendor or
  bills. The failure takes the provider's own error handling and `auto`'s
  fallback, as a real one would.
- **The direct side:** the platform calls the vendor's server, so it is made
  to fail for real:
  - **a revoked key:** the real server, sent a dummy token;
  - **a rate limit or server error:** the server replaced by a public endpoint
    that answers that status;
  - **a timeout:** one that answers after 10 seconds with no MCP reply.

  Only the dummy token is ever sent, and on Claude it lives in a vault of its
  own, so an outage run never touches the vault a run still going reads.
  `run.json` records the outage, and the report names it.
