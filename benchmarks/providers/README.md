# Provider scan: which search backend is better at what

The agent benchmark in `benchmarks/agent/` asks whether an agent does better on
web-router. This asks the question underneath it: on the same questions, which
search backend finds the answer, and on which kinds of question.

Each [SimpleQA](https://github.com/openai/simple-evals) question is one search
per backend, through `evaluate()` and so through the same public `search` path
a caller uses. One fixed judge synthesizes an answer from each backend's results
and grades it. SimpleQA tags every question with a topic (Science and
technology, Politics, Art, Sports, …) and an answer type (Person, Date, Number,
Place, Other), and the report breaks accuracy down by both. That breakdown is
the evidence a routing rule, or the routing guide's advice on which backend to
use, should rest on.

```bash
uv run --all-extras python benchmarks/providers/run.py --limit 300
uv run --all-extras python benchmarks/providers/run.py --questions sealqa-hard
uv run --all-extras python benchmarks/providers/run.py --questions csv --dataset mine.csv \
  --question-column question --answer-column answer
```

`--questions` picks the set: `simpleqa` (the default), `sealqa-0` or
`sealqa-hard` (SealQA, broken down by topic, by how fast the answer changes and
by what search returns for it), or `csv`, your own questions. SealQA withholds
results from its own pages (the dataset, its paper, copies) before judging, and
the report names how many it withheld per backend. Scans combine only on the
same set, and only when they withheld the same pages.

Keys come from the repo's `.env` (or the environment): each backend's own, and
`OPENAI_API_KEY` for the default judge (`openai:gpt-4o`). Every configured
search backend runs unless `--providers` names some; `auto` is left out,
because it would repeat one of the others under a name that says nothing about
who answered.

## Testing a backend's modes

A setup can be more than a backend name:

| Setup | What it runs |
| --- | --- |
| `tavily` | The backend's own default mode |
| `tavily@deep` | web-router's normalized `search_depth` (`lite`, `fast`, `deep`), where the backend maps it |
| `tavily:ultra-fast` | A vendor mode web-router doesn't normalize, sent through `provider_options` from [`modes.json`](modes.json) |
| `nimble:full-content@lite` | Both at once |

```bash
uv run --all-extras python benchmarks/providers/run.py --limit 300 \
    --providers perplexity,perplexity:fast,nimble@lite,nimble,nimble@deep,tavily,tavily@fast,tavily:ultra-fast
```

Each depth runs as its own `evaluate()`, because the library sends one depth to
every backend in a comparison; all of them use the same questions. A mode in
`modes.json` records whether a live call has shown it changing what comes
back (`verified`, `observed`); a mode that is accepted but changes nothing
scores the same as the default and costs a run for nothing.

[`PRICING.md`](PRICING.md) lists every mode's list price with its source and
the date it was checked, for reading accuracy against cost. Its tables are
generated from `src/web_router/data/prices.json`, the list `web_router.prices`
reads; change a price there and run `make prices`.

## Adding backends to a finished scan

A backend that failed during a run, or one added later, can run on the
same questions and be merged in, without paying for the others again:

```bash
uv run --all-extras python benchmarks/providers/run.py --limit 300 --providers exa,tavily --out results/b
uv run --all-extras python benchmarks/providers/run.py --combine results/a results/b --out results/all
```

`--combine` refuses scans with a different question count, seed, search size
or judge, because their rows would not be comparable.

## What it writes

Each run gets its own folder under `benchmarks/providers/results/`
(gitignored), or `--out`:

| File | What's in it |
| --- | --- |
| `report.md` | Overall accuracy with its 95% interval, failures and latency per backend, a recommended `auto` order, then accuracy by topic and by answer type, the best backend in each row marked |
| `summary.json` | The same numbers, for charts |
| `rows.jsonl` | One line per question and backend: the synthesized answer, the grade, the latency, the topic and the answer type |
| `run.json` | The setup the run used |

Accuracy is the judge's grade, latency is timed around the search alone, and
there is no cost column, because no search backend states one. Only the
recommended order (below) reads a cost: the list price from `web_router.prices`, an
estimate, to break ties.

## The recommended `auto` order

After the overall table the report suggests an order for `auto`'s search
chain: the plain backends ranked by [route-eval](#route-eval-replaying-a-router)'s
rule -- correct answers out of every question; within the noise floor, the
cheaper backend first (the cost a row states, else its list price), then the faster; the keyless tier last --
each within the noise floor of the most accurate one marked *best, within the
noise floor* wherever the tie-break put it, and of the rest each marked
*behind* when its 95% interval stops short of the most accurate one's and *not
shown apart* otherwise. It is the same ranking route-eval
prints, over the same rows, so the repository never suggests two orders: each
row is classed as the frozen sets class it, a search that failed on a rate-limit
or quota error is left out (not a miss, nor in the backend's total) and counted
in the table's *Rate limit or quota* column, and the latency tie-break is the
median over every served row, one the judge did not grade included.
A backend counted on fewer than 20 questions is not ranked: as in route-eval,
it is listed after the order as *too few questions* and left out of the chain.
Depth and mode setups (`nimble@deep`, `tavily:ultra-fast`) are left out, since
a chain member is a backend that `auto` sends whatever depth the request asks
for, not a mode. It ends with a snippet to paste into your own router:

```python
WebRouter(provider_config={"auto": {"chain": {"search": ["perplexity", "nimble", "brave"]}}})
```

It is a suggestion from one judge on SimpleQA, and opt-in. The library's
default order changes only by a pull request backed by a scan.

## Reading the numbers

- **One search, not an agent.** The judge sees only what one search returned. A
  backend that ranks the right page second still helps an agent that reads
  further, so this ranks retrieval, not what an agent ends up answering.
- **Intervals are wide.** At 300 questions a backend's accuracy is known to
  about ±5 points; a topic row with 30 questions, to about ±15. Rows under 10
  questions name no best backend.
- **Compare only runs graded by the same judge**, which the report names.

## Comparing extract backends

`extract.py` is the same scan for the other half of the package: handed the
same URLs, which extract backend brings the page back, how often, and how
fast. Each URL is one `benchmark_extract()` fan-out -- the public `extract`
path, one call per backend, timed around the call -- and the rows are measured
facts only. No judge grades the text, so there is no accuracy column: a page is
*returned* when the call answered with a document and *with text* when that
document had any content, and the report says so. A bot wall or a cookie
banner that came back with text counts as a page with text; telling it from
the article is work for a scorer with ground truth, which this scan does not
carry.

```bash
uv run --all-extras python benchmarks/providers/extract.py --limit 100
uv run --all-extras python benchmarks/providers/extract.py --urls social-pdf --limit 0
uv run --all-extras python benchmarks/providers/extract.py --urls mine.csv --url-column link --category-column kind
uv run --all-extras python benchmarks/providers/extract.py --providers nimble,jina --format html
```

`--urls` names a shipped set or a CSV of your own:

| Set | URLs | What's in it |
| --- | --- | --- |
| `general` (the default) | 455 | Reference sites (Wikipedia in several languages, arXiv, GitHub, MDN, PyPI, SEC), news in six languages, government and legal pages, social networks, 60 PDFs and a long tail of small sites; at most 8 URLs per domain. A generic zero-shot shape: a random-looking list, default settings. |
| `social-pdf` | 908 | 575 domains stratified into social and UGC pages (131), PDF documents (407) and a long tail of general pages (370), with a strong non-English share. Stresses unblocking and file handling rather than clean article extraction. |

Both are URL lists sampled from public pages, `url,category` only, under
[`urls/`](urls/). `--limit` samples
them by `--seed`, so two scans on the same seed fetch the same URLs; `--limit 0`
is the whole set.

`--format` is sent to every backend unchanged, so the rows are comparable.
Unset, each backend answers in markdown where it can and its own default where
it cannot (Exa's text), and every backend serves; named, a backend that cannot
return it is that row's *refused*, never a substitution. `--timeout` bounds
each URL's comparison, 120 seconds by default; a backend still unanswered is
that row's *timed out* and the others are unaffected. `--concurrency` is URLs
in flight at once; each fans out to every backend, so a backend's own rate
limit shows up as latency or failures. Credentials come from `.env` or the
environment, every configured extract backend runs unless `--providers` names
some, and `auto` and `max` are left out: each delegates to the others, so
its row would only repeat one of theirs under another name.

It writes the same four files into its own `results/extract-…` folder:
`report.md` with pages-with-text per backend and its 95% interval, the empty,
failed, timed-out and refused counts, p50 and p95 latency and the median length
of what came back, then a recommended `auto` extract order -- ranked on pages
brought back with text out of every URL, a failure counting as a miss, and
opt-in as the search one is -- then the same rate by category, the best backend
in each row marked; `summary.json`, the same numbers; `rows.jsonl`, one line
per URL and backend, appended as each URL finishes; and `run.json`, written
before the first call and again with each URL. `--combine` merges scans of the
same URL file, format and timeout, backends added later, and refuses anything
else. A scan that died keeps the rows it fetched: `--combine` on its folder
alone reports them, saying how many of the URLs asked they cover, and it
cannot be merged with a finished scan, whose rows cover other URLs.

Read it as a measure of who returns a page, not whose page is best. Intervals
are as wide as the search scan's -- at 100 URLs a backend's rate is known to
about ±10 points, a category row with 20 URLs to about ±20 -- and there is no
cost column, because no backend states one; read the rates against
[`PRICING.md`](PRICING.md).

## Comparing answer backends

`answer.py` scans the backends behind `answer()` the same way: one call per
backend per question, the answer each backend wrote graded by the same judge
with the same SimpleQA grader. Nothing is synthesized: the backend's own answer
is what the judge reads.

```bash
uv run --all-extras python benchmarks/providers/answer.py --limit 300 \
  --baseline benchmarks/providers/results/simpleqa-300-v2
```

`--baseline` names a finished search scan of the same questions, seed and
judge; the report then sets the two pipelines side by side -- the answer a
backend wrote, and the answer the judge wrote from a search backend's top
results. Both are graded alike, so accuracy compares. Latency does not quite:
the search side's is the search alone, without the judge writing its answer.
A baseline graded by another judge, or on other questions, is refused.

An answer backend has read SealQA's own pages before it answers, so nothing
can be withheld: a row whose citations include one is marked *leaked*, left
ungraded and out of accuracy, and counted in the report. Cost is shown only
where the backend states it in its response (Perplexity's `usage.cost`), as a
mean over the answers that stated one; everything else is in
[`PRICING.md`](PRICING.md). `--combine` merges answer scans of the same
questions, judge and baseline.

## The tool and effort set

The scans above compare backends within one method. `tools_effort.py` asks
which method, and which research effort, a question is worth: 60 questions,
each through every path a router could pick, every row graded by the same
judge as the published sets.

| Group | Questions | Source |
| --- | --- | --- |
| easy | 10 | SimpleQA: the first 10 of the seed-0 sample behind `simpleqa-300-v2` |
| hard | 20 | SealQA Seal-Hard, sampled with seed 0 |
| synthesis | 20 | FRAMES: the first 20 of the agent benchmark's 300-question seed-2 sample |
| url-given | 10 | 5 more of those SimpleQA questions, each given its first source URL, and 5 more FRAMES questions, given their Wikipedia links |

| Path | Backends | Per question |
| --- | --- | --- |
| `search+extract` | Search on Nimble, Perplexity and Linkup (5 results); the top 3 pages read with Nimble extract; the judge answers from the snippets and the pages. The search is sent the question alone: a url-given question's URLs are for reading, and the `extract` path reads them | 3 rows |
| `extract` | Nimble extract of the given pages; the judge answers from them | 1 row, url-given questions only |
| `answer` | Perplexity, You.com, Parallel, Exa, Linkup and Brave, each backend's own answer | 6 rows; the easy questions reuse `answer-simpleqa-300`'s rows and ask only Brave |
| `research` | Nimble `low`/`medium`/`high`, Parallel `low`/`medium`/`high`/`max`, Linkup `low`, Perplexity `low`/`medium` | 10 rows |

```bash
uv run --all-extras python benchmarks/providers/tools_effort.py --dry-run
uv run --all-extras python benchmarks/providers/tools_effort.py --budget 125
```

`--dry-run` prints the plan -- questions per group, rows and calls per path,
backend and mode, and the worst-case and expected cost of each -- and calls
nothing; it needs no key and no download. The run itself downloads SimpleQA,
SealQA and FRAMES (`--dataset NAME=PATH` reads a local copy instead) and needs
`OPENAI_API_KEY` for the judge and each backend's key; it names any that is
missing before it calls anything.

**Budget.** `--budget` (default $125) is a hard cap on everything the run
spends, the judge included. Before a row starts, its worst case is reserved;
before each call, the call's worst case is drawn from that reservation, and
topped up when the call's prompt makes it dearer. A row that would take what
is spent plus what is reserved past the cap is refused, and the run stops
there: rows in flight finish, and nothing else is sent. A call is charged what
its response states, else its list price from `web_router.prices`, else a
fallback written in `_tools_effort_budget.py` (`UNPRICED`, `FALLBACK`) -- never $0; a call that
fails is charged its worst case, as it may have billed. A call that bills more than its
reserved worst case (a token-billed backend stating a dearer cost) is still
charged what it billed, so spend can pass the cap by at most what the rows in
flight overrun; the next row is then refused. The judge is charged
gpt-4o's list price on the tokens OpenAI reports, its one check before a run included
(skipped when no row is left). The running total is printed
after every row.

**Resuming.** Rows are appended to `results/tools-effort-60/rows.jsonl` as they
finish, and every call's cost to `spend.jsonl` beside it. Run the same command
again -- after a crash, Ctrl-C or a budget stop -- and it skips every row
already written, failed ones included, and counts what was spent before
against the same `--budget`; raise `--budget` to go on past a stop. A row
whose backend answered but whose grading the budget refused is kept, ungraded,
with error `budget`, so a resume never buys that answer twice; a row refused
before anything was paid for is asked again. A row that raises (a bug, a file
that cannot be written) stops new rows too, and is raised once the rows in
flight finish.
`run.json` names the questions by id, and a resumed run refuses a folder that
asked other questions, used another judge or set another `--research-timeout`.

A research run may take `--research-timeout` seconds (default 1200, twice
`research()`'s default: a Linkup run can take a quarter of an hour), and
`--concurrency` rows (default 6) run at once. Experimental until 1.0.

The frozen run's Perplexity research columns (`low` and `medium`) are not
comparable yet: most of their rows failed during the run, and Brave's answer
column, where some rows failed too, rests on fewer questions than the rest. A later run can fill them. The run that froze `tools-effort-60` kept no
latency on its failed rows (`latency_s` is `null` there); the runner now keeps
the time to the failure.

## Frozen results

The scans route-eval and the latency priors rest on are committed, one row per
question and backend, in [`frozen/`](frozen/). A fresh clone gets them, with
their questions, in one command:

```bash
uv run --all-extras python benchmarks/providers/frozen.py join
```

It checks every file against `frozen/SHA256SUMS`, downloads SimpleQA,
SealQA and FRAMES (no key needed), puts each row's question, gold answer and the
dataset's labels back, and writes one `<set>.jsonl` per set to
`results/frozen-joined/`. It stops if any row's `question_id` does not resolve.
`frozen.py verify` runs the checksum step and the failed-row counts alone, and
the unit tests run it too.

The latency in `src/web_router/data/prices.json` comes from these files:
`make latency` pools every set that covers a (provider, method, mode), counts
only the rows with no error, and writes each row's p50, p90, sample size, runs
and date. It leaves out a set that repeats an earlier set's questions by the
same method (`sealqa-hard-254-twin`) and a mixed set (`tools-effort-60`, whose
answer rows are copies), so no question counts twice. A frozen setup with no
price row is reported and left out, never given one.

The committed rows carry no question or answer text, only a `question_id`:
the questions stay with the public datasets they come from, and `join` is how
they come back. SimpleQA is from `openai/simple-evals` (MIT), SealQA from
`vtllms/sealqa` (Apache-2.0) and FRAMES from `google/frames-benchmark`
(Apache-2.0). Experimental until 1.0: the schema may change, and
`frozen/manifest.json` names the version a file follows.

| Set | Questions | Rows | What it is |
| --- | --- | --- | --- |
| `sealqa-hard-254` | 254 | 2540 | Every SealQA Seal-Hard question, one search per backend, ten backends |
| `sealqa-hard-254-twin` | 254 | 508 | The same questions again on two backends: the run-to-run noise floor |
| `simpleqa-300-v2` | 300 | 3600 | 300 SimpleQA questions (seed 0), ten backends, with Keenable and DeepSeek added later on the same questions |
| `answer-simpleqa-300` | 300 | 1500 | The same 300 SimpleQA questions through `answer()`, five backends |
| `tools-effort-60` | 60 | 1150 | [The tool and effort set](#the-tool-and-effort-set): 60 questions through search then read, extract, `answer()` and `research()` at each effort; the easy questions' answer rows are copied from `answer-simpleqa-300`. Most Perplexity research rows and some Brave answers failed during the run; `failed_rows` in the manifest has every count |

Each `frozen/<set>.jsonl.gz` is gzipped JSON lines, sorted by `question_id`,
then provider and mode:

| Field | What it holds |
| --- | --- |
| `set` | The set's name, as above |
| `question_id` | The first 16 hex characters of the SHA-256 of the question's text, normalised: Unicode NFKC, whitespace collapsed to one space, ends stripped. A question given its pages (`tools-effort-60`'s url-given group) hashes its text and its URLs, one space apart |
| `provider` | The backend, as web-router names it |
| `method` | `search` (one search, then the judge writes the answer from its results) or `answer` (the backend's own answer); in `tools-effort-60` also `search+extract` (a search, its top three pages read with Nimble extract, then the judge's answer; a page that could not be read is replaced by its search snippet, so the row is graded on what was read), `extract` (the given pages read, then the judge's answer) and `research` (the backend's report, graded) |
| `mode` | The vendor mode and depth the scan asked for, as `run.py` spells them (`ultra-fast`, `@deep`, `full-content@lite`); `default` when it asked for none, which is the backend's own default for that method. In `tools-effort-60`, a research row's mode is its effort (`low` ... `max`) and a `search+extract` row's is `top3`; every other row is `default` |
| `grade` | `correct`, `incorrect` or `not_attempted`, from the judge; `null` when the row failed before it was graded. Count grades only from rows with `error: null` |
| `latency_s` | Seconds around the search or `answer()` call alone, to the millisecond. On a failed row it is the time to the failure (`0.0` for a call refused before it was sent), so count latency only from rows with `error: null` |
| `cost_usd` | The cost the backend stated in its response, `answer` rows only, where it states one; otherwise `null`. In `tools-effort-60` it is the stated cost, else the list price from `web_router.prices` (a `search+extract` row adds its search and its reads), else `null`. The answer rows copied from `answer-simpleqa-300` are filled the same way. A failed page read's cost and time (about $0.001 each) are not in this run's `search+extract` and `extract` rows; later runs include them |
| `error` | `null`, or the class of what went wrong: `timeout`, `rate_limit`, `quota`, `invalid_request` (refused before sending, such as a query too long for the backend), `http_4xx`, `http_5xx`, `other`, `judge` (the judge failed, not the backend), `budget` (`tools-effort-60`: the backend answered, and the run's budget refused grading it) or `leaked` (an answer citing the set's own pages, left ungraded). The message itself is never kept. The class says how that call ended in this run, not how good the backend is; route-eval skips failed rows |

`frozen/manifest.json` records, per set, the question set and dataset, the
seed and sample size, the search size, the judge (`openai:gpt-4o` for every set),
the row and question counts, `failed_rows` (how many rows failed, `error` not
`null`, per backend and mode; `verify` checks it against the rows), the scans
it was built from with their start times, and `answers_sha256`, a hash of the
gold answers the scans were graded on. `join` refuses a set when the downloaded dataset's answers no longer match
it, and `build` refuses scans graded on different answers, or a backend that
has no row for some question.

Whoever holds the scans rebuilds the folder with
`frozen.py build path/to/results`, which resolves every `question_id` against
the public dataset before it writes, and writes the same bytes for the same
scans. Commit the files it writes, `SHA256SUMS` included.

## route-eval: replaying a router

`route_eval.py` replays a router over the frozen results and scores it
against today's static order. Nothing is called and no key is needed: each
question goes to the router, the router ranks the backends the set has rows
for, and the replay looks up the recorded grade, latency and cost of the one
that served.

```bash
uv run --all-extras python benchmarks/providers/route_eval.py
uv run --all-extras python benchmarks/providers/route_eval.py --set sealqa-hard-254 --router always:nimble
uv run --all-extras python benchmarks/providers/route_eval.py --router mypkg.routers:pick \
    --joined benchmarks/providers/results/frozen-joined
```

By default it scores `sealqa-hard-254` and `simpleqa-300-v2`, SimpleQA being
the control set where routing should gain nothing, and `tools-effort-60`
([below](#tool-choice-and-effort)). A router is `static`
(`auto`'s order today), `oracle` (the backend that got each question right,
cheapest first: the ceiling), `always:<backend>`, or your own callable as
`module:attr` or `path/file.py:attr`. `static` and `oracle` are always scored,
as the yardsticks. Your callable is a router of the library's contract
(`web_router.routing`): it takes a `RouteRequest` -- one candidate per backend
the set has rows for, with its list price and p90, and the method pinned -- and
returns a `RouteDecision`, or `{"decision": ..., "cost_usd": ...}` when it bills
for itself; given a `fit` method, it is called with the training questions
first. The request's `prompt` is the question's text only when `--joined`
names the folder `frozen.py join` wrote; otherwise it is empty and the report
says the prompt was unavailable. Experimental until 1.0.

**It covers provider order for `search` and `answer`, and tool choice and
research effort on `tools-effort-60`.**

How a choice is scored:

- The router's answer goes through `web_router.routing.merge.merge`, the
  merge rule a routed call runs: its ranking first, a backend that is not a
  candidate dropped, then every other candidate in the static order. The
  yardsticks' rankings go through it too. The order is walked as `auto` walks
  its chain. The first backend whose row did not fail serves the question. A
  failed row (`error` not null) is passed over: its time to failure and its
  estimated cost (stated, else list price) are charged, as a budget charges a
  failed attempt, and the question counts as a **fallback** -- as it does when
  the router raises, answers with no well-formed decision, or ranks no
  candidate at all, which falls back to the static order. Those router
  failures also have their own column, and the first exception each router
  raises on a set is printed to stderr. A row whose `error` is `judge` or
  `leaked` is not a failed attempt: the backend answered, so it serves the
  question, ungraded -- never correct, no fallback, counted in its own column.
  A question no backend served is a miss.
- **Accuracy** is correct out of every question; grades and latency come only
  from rows that did not fail. In the recommended order (the ranking each set's
  report ends with, described below), a row that failed
  during the run with a `rate_limit` or `quota` error is left out of its
  backend's questions rather than counted a miss: the call never reached the
  question, so it says nothing about the backend's answer. The replay still
  passes over such a row, as `auto` would.
- **Cost** is the cost the row states (`answer`), else the list price from
  `web_router.prices` (an estimate). An unknown price is unknown, never $0: the
  mean is over the questions whose cost is known, and the count unknown is
  shown.
- **Latency** adds the failed attempts before the one that served, and the
  router's own time. A question where any attempt's latency is unrecorded is
  left out of the mean and counted as unknown, as cost is. The router's own time (**overhead**) and any cost it
  states are charged on every question, so a slow or paid router does not look
  free.
- **Regret** is the oracle's accuracy, or the static order's, minus the
  router's, in points.

Every router is scored on one seeded split (`--seed 0`, half held out by
`--test-fraction`), in-sample and held out side by side: on SealQA, routing by
gold labels once scored 75 in-sample and 46 held out. The **noise floor** is
the widest accuracy gap one backend shows between `sealqa-hard-254` and its
twin, the same questions run twice (WebIQ, 51 vs 54 of 254: 1.2 points). Each
method is ranked and judged with its own rerun's spread where one is frozen
(`RERUNS` in `route_eval_sets.py`), else with search's; that twin is the only
rerun today, so `answer` and `tools-effort-60` fall back to it, and each set's
report names the floor it used. It is rerun noise, not sampling
error, so a held-out verdict needs both: a router is a *gain* only when its
margin over the static order is wider than the floor and the static order's
held-out accuracy falls below the router's 95% interval, a *loss* only when
the margin is under minus the floor and the static order sits above that
interval, and *no gain* otherwise.

Each set ends with the **recommended order**, ranked on every question of the
set, since nothing is fit to it: by correct out of every question; within the
noise floor of the best not yet placed, the cheaper backend at known cost
first, then the faster, then the name. An unknown or $0 cost never moves a backend up, and the keyless tier
stays last. A backend counted on fewer than 20 questions (`MIN_QUESTIONS` in
`route_eval_rank.py`, which says why 20) is not ranked: it is listed after the
ranked ones as *too few questions*. `run.py`'s recommended order applies the
same minimum, so a scan of fewer than 20 questions suggests no order. AIQ and the
cost and latency curves wait for the first real router. `--json PATH` writes
the numbers as well.

### The default `auto` orders: `make chains`

`auto`'s keyed search order and answer's order are not written by hand. `make
chains` (`chains.py`, beside `route_eval.py`) writes them to
`src/web_router/data/chains.json`, each method's recommended order on its
ranking set, over all of its questions (twice the held-out half's data, so less
noise). Routers are still scored on the held-out half, so ranking the baseline on
every question makes it slightly harder for a router to beat there, on purpose:

- **search** ranks on `sealqa-hard-254`. On SimpleQA, the control, every keyed
  backend scores 81-95% and the previous order already came from it; SealQA
  hard is where they differ, and the noise floor is measured on its own rerun.
  Pooling the two would need every member on both, and Keenable and DeepSeek
  have no SealQA rows.
- **answer** ranks on `answer-simpleqa-300`, the one answer set.

The rule is the recommended order's, above; `chains.json` states it as `rule`,
and the minimum count as `min_questions`. Each method's entry names the floor
it was ranked with as `noise_floor`: search's own rerun; answer has none
frozen, so it is ranked with search's (`"fallback": true`). A member counted on
too few questions keeps its place after the ranked ones, as
`too_few_questions`.

On `sealqa-hard-254`, judged by `openai:gpt-4o`, frozen from the run of
2026-10-07, WebIQ (51 of 254), Nimble (49) and Perplexity (48) sit within the
floor of each other, so the priced and faster go first: Perplexity, Nimble,
then WebIQ, whose price is unknown. Perplexity leads by the narrowest margin:
its 48 sits exactly on the floor (3 questions, 1.2 points) below WebIQ's 51, so
one answer fewer for it, or one more for WebIQ, in a new frozen run would put
Nimble first for every keyed caller. Linkup (47) follows, then Tavily (42), Exa
and Parallel (41 each), Brave (37) and You.com (29). The order this replaced,
from a SimpleQA scan, was Perplexity, Nimble, Linkup, Tavily, Brave, WebIQ,
Parallel, Exa, You.com.

On `answer-simpleqa-300`, judged by `openai:gpt-4o`, frozen from the run of
2026-11-01, Perplexity (286 of 300), You.com (285), Parallel (285) and Linkup
(283) sit within the floor, cheapest first with Parallel's unknown price last;
Exa (281) does not. The order this replaced, cheapest first at list price, was
Perplexity, Exa, You.com, Linkup, Parallel, Brave.

A member the set has no rows for (Keenable, DeepSeek and AgentCore for search,
Brave for answer) keeps its place after the ranked ones and is listed as
`unmeasured`; the keyless search tier stays in `constants.py`, unranked.
Extract's order is measured on pages returned, not graded, and research's and
browse's are unmeasured, so all three stay hand-ordered. `chains.py --check` exits 1 when
the file is stale, and a unit test runs it, so a change to the frozen results
or the price list that moves an order fails until `make chains` is run and the
README's tables follow. For the full report behind both orders:

```bash
uv run --all-extras python benchmarks/providers/route_eval.py --set sealqa-hard-254 --set answer-simpleqa-300
```

### Tool choice and effort

On [`tools-effort-60`](#the-tool-and-effort-set), whose rows carry several
methods, a candidate is a **path**: method, backend and mode, written
`method:backend[:mode]` -- `search+extract:nimble:top3`, `extract:nimble`,
`answer:perplexity`, `research:parallel:low` (a research row's mode is its
effort). A question's candidates are the paths it has rows for, so `extract`
is one only on the ten questions given their pages. A router is shown one
candidate per method and backend, with no method pinned, and decides the
method, the backends and research's `effort`; the merge rule picks one method,
and each backend runs its path at that effort, or with none set, its
default-mode row, else the price list's default effort, as a real call would;
a one-method research set is matched the same way, its rows being default-mode. A backend with no row at that
effort cannot be scored: it keeps its place in the order as a miss with no
cost, so a later backend serving still counts as a fallback, and it is counted
in the summary's `unscorable`. A decision never moves to another method, so the
paths of the others are not tried after it; a decision that fails or decides
nothing walks the static order of the merged method only (search's, unpinned),
as a routed call would, not the full static path order. On a question given its
pages the request's `urls` is the placeholder `frozen:pages`, since the frozen
rows keep no page URLs. The yardsticks still rank paths, walked as ranked and then in
the static path order. The scores, the split, the noise floor and the
recommended order (here of paths) are the same as above.

```bash
uv run --all-extras python benchmarks/providers/route_eval.py --set tools-effort-60
```

- **Static** is what a caller with no router does today: `search` through
  `auto`'s order, then read the top pages -- the first `search+extract` path
  (Perplexity's, in today's order). The other paths follow only as its
  fallback order: extract, answer, then research, each by its `auto` order,
  research lowest effort first. The cheapest path would be a weaker
  yardstick: no caller gets it by default.
- **Oracle** is the path that got each question right, cheapest, then fastest.
- The example routers, scored when no `--router` is given, are
  `always:research:parallel:low` and `always:answer:perplexity`.
- A failed row counts as on any set: in the replay it is passed over. In the
  recommended order and the effort table, a row that failed during the run
  with `rate_limit` or `quota` is left out of the count and shown apart, as
  above, so a path with many such rows rests on few questions.
- **Research by effort** closes the set's report: each research backend at
  each effort, correct out of the questions counted, mean cost, median latency
  and the rows left out -- the evidence for mapping a budget to an effort.

60 questions is small. Each held-out half is 30, where one question is 3.3
points and the 95% interval runs some 30 points wide, which is why a verdict
needs the static order outside it: here neither example router is a gain or a
loss. The noise floor is search's: this set has no twin run. A path counted on
fewer than 20 questions is listed as *too few questions*, not ranked: today
`extract:nimble` (asked on 10), `research:perplexity:low` (4 counted) and
`research:perplexity:medium` (19).
A set that mixes methods other than these paths is refused by name.

### Tool confusion and comparing routers

Every set's report also has, held out, each router's **cost-weighted tool
confusion matrix** and **where routers disagree**
(`route_eval_matrix.py` and `route_eval_compare.py`, rendered by
`route_eval_report_matrix.py`).

- **The matrix** counts each held-out question by the tool the router's
  choice ran (rows; `unserved` when nothing served it) against the best tool
  (columns): the tool of the oracle's path, the one that got the question
  right, cheapest then fastest, or *none right* when no path did. Each cell
  reads *questions (right), overspend, time over*: what the router's choices
  cost and took beyond the oracle's on those questions, as the replay charged
  them. A cheaper or faster choice adds nothing there; it pays in accuracy,
  which has its own column. A dear `research` moves a cell by dollars, a dear
  `search` by cents, so the dear mistakes stand out. An extra with either side
  unknown is left out of the sum and counted unknown, as cost is everywhere.
- **Off the oracle, two counts.** A question's *right tools* are every tool
  with a path that got it right; a choice is on target when its tool is one of
  them. *Wrong tool, wrong answer* counts the questions some tool got right and
  the router's tool did not: the accuracy lost to tool choice. *Right, not the
  cheapest tool* counts the questions it got right on another tool than the
  best: pure cost regret, charged against the cheapest right path. A wrong
  answer from a right tool on another backend is neither; it is outside the
  tool matrix. Above the matrices, one row per router gives both counts, the
  cost regret, the overspend on every question and per question, the accuracy
  lost to the oracle from every cause, and the time over per question. The
  signed extras, which net a cheaper wrong choice against a dearer one, are in
  `--json` only. On a one-method set every path is one tool, so the matrix is
  one row: the router's overspend over the oracle.
- **The oracle is an upper bound.** It is built from one frozen sample per
  path, so a path that got a question right once by chance makes its tool a
  right one, and the oracle's accuracy and the counts above lean optimistic.
- **The comparison** sets each router beside `static` and beside every router
  named with `--against` (scored too), each pair once, on the same held-out
  questions: how often the served path differs (another backend of the same
  tool counts) and how often the tool does, which of the two alone was right
  there with the exact McNemar p-value of those two counts, each one's accuracy
  with its Wilson 95% interval, and each one's mean cost over the questions
  where both costs are known (the rest counted once, for the pair). On 30
  held-out questions a gap of 15 points can still be noise; the p-value
  and the intervals say so. Each pair's disagreements follow by tool pair.
  `--against oracle` is refused: the matrix above already sets every router
  against the oracle.
- **Every question, too.** A router with no `fit` method learns nothing from
  the training split -- `static`, `always:` and a file router without one,
  such as the rules router -- so it is also scored on every question
  (`every_question` in `--json`) and each pair of them compared there, where
  the held-out split would only cost questions. A router with `fit` is scored
  held out only. `--json` keeps every router's held-out choices (`choices`),
  an unfit router's choices on every question, and each pair's disagreeing
  questions.

One command reproduces the numbers. It reads only the committed frozen rows, so
it calls nothing and costs $0; `frozen.py join` (once) puts the question text
back from the public sets, for a router that reads the prompt, such as the
rules router in `examples/router_rules.py`:

```bash
uv run --all-extras python benchmarks/providers/frozen.py join
uv run --all-extras python benchmarks/providers/route_eval.py --set tools-effort-60 \
    --router path/to/your_router.py:route --against examples/router_rules.py:route \
    --joined benchmarks/providers/results/frozen-joined
```

That scores your router, the rules router, `static` and `oracle`, and compares
your router with the rules router and with the static order, and the rules
router with the static order. Your router loads as any route-eval router does:
`module:attr` or `path/file.py:attr`; `--router features` scores the built-in
default.

## Router latency: which tools a router should order

A router attached with `router=` is asked for the backend order of every call
to a tool in `route_tools`, and each ask adds the router's time to the call.
`router_latency.py` times routers and weighs each against each tool. It runs
outside CI.

```bash
uv run --all-extras --with scikit-learn python benchmarks/providers/router_latency.py --router features --router sklearn
uv run --all-extras python benchmarks/providers/router_latency.py --router llm --router jev \
    --llm-model <model> --llm-price <in>,<out> --jev-price <usd> --dry-run
uv run --all-extras python benchmarks/providers/router_latency.py \
    --router remote:http://127.0.0.1:8765/route --remote-rtt-ms 40
```

**The rule.** A tool asks the router for its backend order only when the
router's p50 is below 25% of the tool's p50 (`--share`). **A tool's p50** is
the p50 of the first backend in its default `auto` chain, with a key for every
backend (`--chain here`: the keys this machine holds), from the price list's
measured latency. Only search and answer are measured; for extract, research
and browse the verdict is a judgement call, and the script never makes a
number up. The latency belongs to the router, not the tool, so the result is a
row per router: a router declares the tools it fits as its own `route_tools`
(see the README's Smart routing), and a caller's `route_tools` always wins.

**How it times.** A router is one of `features` (the built-in default router),
`llm` (the built-in `router="llm"`, on the preset `LLM_ROUTER_PRESET` names,
`openai` unless set), `sklearn` and `jev` (the examples in `examples/`), `remote:<url>` (a
router service in the wire format, schema 1; `--remote-rtt-ms` adds the same
service behind a simulated round trip, three of them on a new connection), or
your own `module:attr`. Each is called through the library's own router call,
so a sample is the `router_ms` a decision record holds. The prompts are
route-eval's public questions, `tools-effort-60` and `simpleqa-300-v2`, put
back by `frozen.py join`; each request is the one route-eval shows a router,
one candidate per backend the set has rows for. Every router in one run is
timed in the same session, interleaved in a seeded random order: a first
call, reported apart, then `--warmup` calls dropped, then `-n` timed calls
(200 by default). A call that reaches the router's deadline (its `timeout_s`,
or `--deadline-s` when that is shorter; the added wall time always keeps
`timeout_s`) is censored: it counts toward the timeout rate and ranks above
every answer, never as a value; an error ranks the same way, so a router
that mostly fails has no p50 to weigh. `--cold` calls each build the router
anew, and `--e2e` stubbed searches through `auto`, routed and with
`route_tools=()`, give the wall time routing adds, candidates and the thread
hop included. `--dry-run` prints each router's calls, the variables it needs
and an estimated cost, and calls nothing. Each run writes a JSON file and its
table to `results/router-latency/`.

**Results.** 2026-11-09 14:44 UTC, one async run (`--async`) from one host:
an Apple M4 Pro (12 CPUs), macOS 26.6.2, Python 3.12.13, web-router 0.10.0,
scikit-learn 1.9.1, openai 3.45.0, httpx 0.28.1. Each router was called 200
times after a first call and 5 warm-up calls, interleaved in a seeded random
order, on `tools-effort-60` and `simpleqa-300-v2`. The LLM router is
`examples/router_llm.py` as it was then, on the openai SDK, on OpenAI's `gpt-4.1-mini`; Jev is
`examples/router_jev.py` on `jev-1.14.0`; the sklearn router is the example's
toy-trained pipeline; remote is the built-in `remote` router asking
`examples/router_remote_server.py` on loopback, then the same behind a
simulated 40 ms round trip. The run cost about $0.10.

| Tool | First backend in `auto` | p50 s | p90 s |
|---|---|--:|--:|
| search | perplexity | 0.316 | 1.719 |
| answer | perplexity | 1.874 | 2.128 |
| extract, research, browse | nimble, nimble, browser-use | not measured | not measured |

| Router | p50 ms | p90 ms | Timeouts | First call ms | Added wall time p50 ms | search | answer |
|---|--:|--:|--:|--:|--:|---|---|
| rules (removed; measured before `features` replaced it) | 0.375 | 0.553 | 0% | 0.23 | 0.98 | consult (0.12%) | consult (0.02%) |
| sklearn | 2.07 | 2.61 | 0% | 1.9 | 2.5 | consult (0.65%) | consult (0.11%) |
| llm (`gpt-4.1-mini`) | 1223 | 1714 | 0% | 2084 | 1389 | skip (387%) | skip (65%) |
| jev (`jev-1.14.0`) | 242 | 271 | 0.5% | 278 | 237 | skip (76%) | consult (13%) |
| remote, loopback | 3.17 | 4.1 | 0% | 56 | 3.75 | consult (1.0%) | consult (0.17%) |
| remote, 40 ms RTT (simulated) | 47 | 48 | 0% | 135 | 47 | consult (15%) | consult (2.5%) |

The 25% share held: no router sits near it. The nearest are a 40 ms link on
search (15%) and the LLM router on answer (65%), so any share from 16% to 64%
gives these same verdicts.

What the examples declare from it: search and answer as measured, extract,
research and browse by judgement, since they take seconds to minutes. The
former rules router and the sklearn example pass every measured tool and declare no
`route_tools`. The LLM router declares `("research", "browse")` (it never picks
extract with no URL in the prompt), and Jev `("answer", "extract", "research",
"browse")`. A remote service declares its own through `router_config`; at the
latencies above, loopback and a 40 ms link both fit search and answer. A
`plan()` always asks the router. Router latency moves with the model, the hour
and the network, so rerun it for yours. Experimental until 1.0, like the
router contract.

## Keeping results

Each run of either scan writes `rows.jsonl`, `summary.json` and `report.md` to
its own folder under `results/`, which git ignores. Keep the reports you want
to compare against the next run.
