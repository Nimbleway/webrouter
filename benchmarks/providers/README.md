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
the date it was checked, for reading accuracy against cost. No code reads it.

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

## Keeping results

Each run writes `rows.jsonl`, `summary.json` and `report.md` to its own folder
under `results/`, which git ignores. Keep the reports you want to compare
against the next run.
