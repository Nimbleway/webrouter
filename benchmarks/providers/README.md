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

A backend whose account was out of credit, or one added later, can run on the
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

Nothing is estimated. Accuracy is the judge's grade, latency is timed around
the search alone, and there is no cost column, because no backend states one.

## The recommended `auto` order

After the overall table the report suggests an order for `auto`'s search
chain: the plain backends by overall accuracy, each marked *behind* when its
95% interval stops short of the best one's and *not shown apart* otherwise.
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
comparable yet: most of their rows failed during the run (56 and 41 of 60),
and Brave's answer column, 22 of 60 failed, rests on fewer questions than the
rest. A later run can fill them.

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

The committed rows carry no question or answer text, only a `question_id`:
the questions stay with the public datasets they come from, and `join` is how
they come back. SimpleQA is from `openai/simple-evals` (MIT) and SealQA from
`vtllms/sealqa` (Apache-2.0) and FRAMES from `google/frames-benchmark` (Apache-2.0); the rows carry ids, grades and timings, no
dataset text. Experimental until 1.0: the schema may change, and
`frozen/manifest.json` names the version a file follows.

| Set | Questions | Rows | What it is |
| --- | --- | --- | --- |
| `sealqa-hard-254` | 254 | 2540 | Every SealQA Seal-Hard question, one search per backend, ten backends |
| `sealqa-hard-254-twin` | 254 | 508 | The same questions again on two backends: the run-to-run noise floor |
| `simpleqa-300-v2` | 300 | 3600 | 300 SimpleQA questions (seed 0), ten backends, with Keenable and DeepSeek added later on the same questions |
| `answer-simpleqa-300` | 300 | 1500 | The same 300 SimpleQA questions through `answer()`, five backends |
| `tools-effort-60` | 60 | 1150 | [The tool and effort set](#the-tool-and-effort-set): 60 questions through search then read, extract, `answer()` and `research()` at each effort; the easy questions' answer rows are copied from `answer-simpleqa-300`. Most Perplexity research rows failed during the run (56 of 60 `low`, 41 of 60 `medium`), and 22 of 60 Brave answers; `failed_rows` in the manifest has every count, and route-eval leaves them out |

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
| `cost_usd` | The cost the backend stated in its response, `answer` rows only, where it states one; otherwise `null`. In `tools-effort-60` it is the stated cost, else the list price from `web_router.prices` (a `search+extract` row adds its search and its reads), else `null` The answer rows copied from `answer-simpleqa-300` are filled the same way. A failed page read's cost and time (about $0.001 each) are not in this run's `search+extract` and `extract` rows; later runs include them |
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

## Keeping results

Each run of either scan writes `rows.jsonl`, `summary.json` and `report.md` to
its own folder under `results/`, which git ignores. Keep the reports you want
to compare against the next run.
