"""Turn an agent benchmark's ``summary.json`` into one HTML page for people who read results, not rows.

``report.md`` explains every figure to whoever runs the benchmark; this page
shows the same run to a stakeholder: who won on each axis, by how much, and
whether the run can tell. It is built from the summary alone, so it shows no
number the summary does not hold -- no estimated cost, no score of its own.

    uv run python benchmarks/agent/page.py benchmarks/agent/results/<run>

writes ``<run>/page.html``. The page is a fragment in the Artifact page
contract's shape -- a ``<title>`` and ``<style>`` first, no ``<html>`` or
``<body>`` of its own -- so it can be published as it is. Its one outside
request is Google Fonts, which an Artifact page may load; offline, every face
falls back to a system font and nothing else changes.
"""

from __future__ import annotations

import json
import sys
from html import escape
from pathlib import Path
from typing import Any

QUESTION_SET_TEXT: dict[str, dict[str, str]] = {
    "frames": {
        "name": "FRAMES",
        "source": "google/frames-benchmark",
        "kind": "multi-hop",
        "category": "reasoning type",
        "category_note": (
            "FRAMES tags each question with the kinds of reasoning it takes. A question with several tags counts "
            "under each, so the rows overlap."
        ),
    },
    "simpleqa-verified": {
        "name": "SimpleQA Verified",
        "source": "google/simpleqa-verified at commit 0dc97e0d",
        "kind": "single-fact",
        "category": "topic",
        "category_note": "SimpleQA Verified gives each question one topic.",
    },
    "sealqa-0": {
        "name": "SealQA Seal-0",
        "source": "vtllms/sealqa (seal-0) at commit 0c298f51",
        "kind": "search-adversarial",
        "category": "freshness",
        "category_note": "SealQA tags each question with how fast its answer changes: never, slowly or fast.",
    },
    "sealqa-hard": {
        "name": "SealQA Seal-Hard",
        "source": "vtllms/sealqa (seal-hard) at commit 0c298f51",
        "kind": "search-adversarial",
        "category": "freshness",
        "category_note": "SealQA tags each question with how fast its answer changes: never, slowly or fast.",
    },
    "csv": {
        "name": "your own questions",
        "source": "a CSV passed with --dataset",
        "kind": "custom",
        "category": "topic",
        "category_note": "Grouped by the CSV's topic column, where it has one.",
    },
}
"""What a report says about each ``--questions`` set. A run from before the choice existed asked FRAMES."""


TOOL_KINDS: tuple[tuple[str, str], ...] = (
    ("native", "the platform's built-in web search"),
    ("web-router:", "web-router's search and extract"),
    ("mcp:", "vendors' own hosted MCP servers"),
    ("direct:", "vendors' own APIs, called directly"),
    ("no-search", "no web tool at all"),
)
"""How each kind of arm is named in an introduction, keyed by what its name starts or ends with."""


def tool_kinds(arms: list[str]) -> str:
    """The web tools a run's arms actually had, as a phrase -- only the kinds that ran, so no comparison is claimed
    that did not take place."""
    specs = [arm.split("/", 1)[-1] for arm in arms]
    found = [label for key, label in TOOL_KINDS if any(s.startswith(key) or s.endswith(key) for s in specs)]
    return ", ".join(found[:-1]) + (" or " if len(found) > 1 else "") + found[-1] if found else "its web tools"


def question_set(setup: dict[str, Any]) -> dict[str, str]:
    return QUESTION_SET_TEXT[setup.get("questions") or "frames"]


SUMMARY_FILE, PAGE_FILE = "summary.json", "page.html"
CLAUDE, OPENAI = "claude", "openai"
PLATFORM_NAME = {CLAUDE: "Claude", OPENAI: "OpenAI"}
PLATFORM_CLASS = {CLAUDE: "p-claude", OPENAI: "p-openai"}
NATIVE = {CLAUDE: "claude-native", OPENAI: "openai-native"}
PLATFORM_SEPARATOR = "/"
PAGE_TITLE = "Agent Search Benchmark"

CLEAR, NOISE, TOO_FEW, TIED = "clear", "within noise", "too few runs", "tied"
VERDICT_TEXT = {CLEAR: "Clear", NOISE: "Not separated", TOO_FEW: "Too few runs", TIED: "Tied"}
VERDICT_CLASS = {CLEAR: "v-clear", NOISE: "v-noise", TOO_FEW: "v-few", TIED: "v-noise"}
NO_NATIVE = "no native arm in this run"

HEADLINE_AXES = ("accuracy", "input_tokens_p50", "latency_p50", "list_cost_p50")
"""The axes a stakeholder asks about first: is it right, what does it read, how long, what does it cost."""
NO_FIGURE = {
    "list_cost_p50": "No cost figure: this platform reports tokens, not a cost, and this page never estimates one.",
}
NO_FIGURE_DEFAULT = "No arm on this platform has a figure for this."

ACCURACY_TICK = 0.1
"""Accuracy charts start at the tenth below the lowest interval, not at zero: a dot-and-whisker has no bar to anchor."""


def render(summary: dict[str, Any]) -> str:
    """The whole page, from a summary ``report.build()`` produced."""
    setup = summary.get("setup", {})
    platforms = _platforms(summary)
    arms = [{**a, "platform": a.get("platform") or platforms[0]} for a in summary.get("arms", {}).values()]
    both = len(platforms) > 1
    body = [
        _intro(setup, platforms, both),
        _alerts(summary),
        _headline(summary, platforms),
        _winners(summary, platforms, both),
        _charts(arms, platforms),
        _results(arms, both),
        _backends(setup),
        _caveats(both),
    ]
    return f'<title>{PAGE_TITLE}</title>\n{_STYLE}\n<main class="page">\n' + "\n".join(body) + "\n</main>\n"


def _platforms(summary: dict[str, Any]) -> list[str]:
    setup = summary.get("setup", {})
    return list(summary.get("platforms") or setup.get("platforms") or [setup.get("platform", CLAUDE)])


def _platform_setup(setup: dict[str, Any], platforms: list[str]) -> dict[str, dict[str, Any]]:
    if "platform_setup" in setup:
        return dict(setup["platform_setup"])
    return {platforms[0]: {k: setup[k] for k in ("model", "surface") if k in setup}}


def _own_family(setup: dict[str, Any], platforms: list[str]) -> list[str]:
    flag = setup.get("judge_same_family")
    if isinstance(flag, list):
        return flag
    return platforms[:1] if flag else []


def _spec(arm: str) -> str:
    prefix, sep, rest = arm.partition(PLATFORM_SEPARATOR)
    return rest if sep and prefix in PLATFORM_NAME else arm


def _is_native(arm: str) -> bool:
    return arm in NATIVE.values()


def _alerts(summary: dict[str, Any]) -> str:
    alerts = summary.get("alerts") or []
    if not alerts:
        return ""
    items = "".join(f"<li><code>{escape(a['arm'])}</code>: {escape(a['reason'])}.</li>" for a in alerts)
    return (
        '<section class="alert" role="alert"><h2>Arms whose tools or runs failed</h2><p class="section-lede">'
        "Their tools or runs mostly failed, so their figures measure the failure, not the backend. They stay in "
        f"the tables, but are left out of the headline and the winners.</p><ul>{items}</ul></section>"
    )


def _intro(setup: dict[str, Any], platforms: list[str], both: bool) -> str:
    agents = " and ".join(PLATFORM_NAME[p] for p in platforms)
    lede = (
        f"{agents} agents answered the same {question_set(setup)['kind']} {question_set(setup)['name']} questions, "
        f"once per arm: {tool_kinds(setup.get('arms') or [])}. On each platform only the web tools changed; the "
        "model and the prompt stayed the same, so a gap between two arms of one platform is the tools'."
    )
    facts = [
        (
            "Questions",
            f"{setup.get('asked') or setup.get('limit')} from {question_set(setup)['name']}, seed {setup.get('seed')}",
        ),
        ("Repeats", f"{setup.get('repeats')} per question per arm"),
    ]
    for platform, ps in _platform_setup(setup, platforms).items():
        label = f"{PLATFORM_NAME.get(platform, platform)} model" if both else "Model"
        facts.append((label, f"{ps.get('model')} on {ps.get('surface')}"))
    facts.append(("Results per search", str(setup.get("max_results"))))
    judge = f"{setup.get('judge')}, one judge for every arm"
    family = _own_family(setup, platforms)
    note = ""
    if family:
        who = " and ".join(PLATFORM_NAME.get(p, p) for p in family)
        note = (
            f'<p class="note">The judge is from the same model family as the {escape(who)} agents, so it is not '
            "independent of them. Its accuracy figures compare only with runs this same judge graded.</p>"
        )
    facts.append(("Judge", judge))
    rows = "".join(f"<div><dt>{escape(k)}</dt><dd>{escape(str(v))}</dd></div>" for k, v in facts)
    return (
        '<header class="intro">'
        '<p class="eyebrow">web-router · agent benchmark</p>'
        f"<h1>Built-in web search against web-router, on {escape(agents)}</h1>"
        f'<p class="lede">{escape(lede)}</p>'
        f'<dl class="facts">{rows}</dl>{note}'
        "</header>"
    )


def _headline(summary: dict[str, Any], platforms: list[str]) -> str:
    winners = summary.get("winners") or []
    cards = []
    for platform in platforms:
        items = []
        for axis in HEADLINE_AXES:
            entry = next((w for w in winners if w["platform"] == platform and w["axis"] == axis), None)
            items.append(_headline_item(axis, entry))
        cards.append(
            f'<article class="card {PLATFORM_CLASS.get(platform, "")}">'
            f"<h3>{escape(PLATFORM_NAME.get(platform, platform))}</h3>"
            f'<ul class="axes">{"".join(items)}</ul></article>'
        )
    return (
        '<section><h2>Headline</h2><p class="section-lede">Each line names the best arm on that axis and how far it '
        "is from native search. <strong>Clear</strong> means this run tells the two apart; <strong>not "
        "separated</strong> means it does not, however large the gap looks.</p>"
        f'<div class="cards">{"".join(cards)}</div></section>'
    )


def _headline_item(axis: str, entry: dict[str, Any] | None) -> str:
    if entry is None:
        label = {"accuracy": "Accuracy", "input_tokens_p50": "Input tokens (median)", "latency_p50": "p50 latency"}
        name = label.get(axis, "Anthropic list cost (median)")
        return (
            f'<li><span class="axis">{escape(name)}</span>'
            f'<span class="none">{escape(NO_FIGURE.get(axis, NO_FIGURE_DEFAULT))}</span></li>'
        )
    value = _value(entry["unit"], entry["axis"], entry["value"])
    winner = entry["winner"]
    tied = entry.get("tied_with") or []
    if tied:
        names = ", ".join(f"<code>{escape(_spec(arm))}</code>" for arm in [winner, *tied])
        detail = f"Tied: {names}"
        vs = entry.get("vs_native")
        if vs:
            # A tie between web-router arms still says how far they are from native, the comparison that matters.
            detail += f", {escape(_margin(entry, vs))} against native"
        margin = vs or entry.get("runner_up")
        verdict = _chip(margin["verdict"]) if margin else ""
    elif _is_native(winner):
        runner = entry.get("runner_up")
        detail = "Native search is best"
        if runner:
            detail += f"; next is <code>{escape(_spec(runner['arm']))}</code>, {escape(_margin(entry, runner))}"
        verdict = _chip(runner["verdict"]) if runner else ""
    elif entry.get("vs_native"):
        vs = entry["vs_native"]
        detail = f"<code>{escape(_spec(winner))}</code>, {escape(_margin(entry, vs))} against native"
        verdict = _chip(vs["verdict"])
    else:
        why = NO_NATIVE if entry.get("native", True) is None else "native has no figure to compare"
        detail = f"<code>{escape(_spec(winner))}</code>; {why}"
        verdict = ""
    return (
        f'<li><span class="axis">{escape(entry["label"])}</span>'
        f'<span class="figure">{escape(value)}</span>'
        f'<span class="detail">{detail}</span>{verdict}</li>'
    )


def _winners(summary: dict[str, Any], platforms: list[str], both: bool) -> str:
    winners = summary.get("winners") or []
    if not winners:
        return ""
    blocks = []
    for platform in platforms:
        rows = []
        for entry in (w for w in winners if w["platform"] == platform):
            runner = entry.get("runner_up")
            vs = entry.get("vs_native")
            if vs is not None:
                native = f"{escape(_margin(entry, vs))} {_chip(vs['verdict'])}"
            elif entry.get("native", True) is None:
                native = f'<span class="muted">{NO_NATIVE}</span>'
            elif entry.get("native_has_figure", True):
                native = '<span class="muted">native is best</span>'
            else:
                native = '<span class="muted">n/a (native has no figure)</span>'
            runner_cell = (
                f"{escape(_margin(entry, runner))} vs <code>{escape(_spec(runner['arm']))}</code> "
                f"{_chip(runner['verdict'])}"
                if runner
                else '<span class="muted">only one arm</span>'
            )
            rows.append(
                f'<tr><th scope="row">{escape(entry["label"])}</th>'
                "<td>"
                + f"<code>{escape(_spec(entry['winner']))}</code>"
                + (
                    " (tied with " + ", ".join(f"<code>{escape(_spec(a))}</code>" for a in entry["tied_with"]) + ")"
                    if entry.get("tied_with")
                    else ""
                )
                + "</td>"
                f'<td class="num">{escape(_value(entry["unit"], entry["axis"], entry["value"]))}</td>'
                f"<td>{runner_cell}</td><td>{native}</td></tr>"
            )
        heading = f"<h3>{escape(PLATFORM_NAME.get(platform, platform))}</h3>" if both else ""
        blocks.append(
            f'<div class="block {PLATFORM_CLASS.get(platform, "")}">{heading}<div class="scroll"><table>'
            '<thead><tr><th>Axis</th><th>Winner</th><th class="num">Value</th><th>Over the runner-up</th>'
            f"<th>Over native search</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></div>"
        )
    lede = (
        "Every axis, ranked within each platform. Margins carry a verdict from a paired bootstrap over the "
        "questions both arms served."
        if both
        else "Every axis, ranked. Margins carry a verdict from a paired bootstrap over the questions both arms served."
    )
    return f'<section><h2>Winners by axis</h2><p class="section-lede">{lede}</p>{"".join(blocks)}</section>'


def _charts(arms: list[dict[str, Any]], platforms: list[str]) -> str:
    legend = "".join(
        f'<span class="key {PLATFORM_CLASS.get(p, "")}"><i></i>{escape(PLATFORM_NAME.get(p, p))}</span>'
        for p in platforms
    )
    legend += '<span class="key native"><i></i>Native search (hollow)</span>'
    charts = [
        _accuracy_chart(arms, platforms),
        _bar_chart(
            arms,
            platforms,
            "input_tokens_p50",
            "Input tokens per run (median)",
            "What the model read over a whole run. Fewer is cheaper; each platform counts with its own tokenizer.",
            lambda v: f"{v:,.0f}",
        ),
        _latency_chart(arms, platforms),
    ]
    return (
        f'<section><h2>The figures</h2><div class="legend" aria-label="Legend">{legend}</div>'
        f'<div class="charts">{"".join(charts)}</div></section>'
    )


def _chart_frame(title: str, lede: str, rows: str, omitted: list[str], scale: str = "") -> str:
    note = ""
    if omitted:
        names = ", ".join(f"<code>{escape(a)}</code>" for a in omitted)
        note = f'<p class="omitted">Not shown, no figure: {names}.</p>'
    return (
        f'<figure class="chart"><figcaption><strong>{escape(title)}</strong><span>{escape(lede)}</span>'
        f"</figcaption>{scale}{rows}{note}</figure>"
    )


def _grouped(arms: list[dict[str, Any]], platforms: list[str], row: Any) -> str:
    groups = []
    for platform in platforms:
        mine = [row(a) for a in arms if a["platform"] == platform]
        mine = [r for r in mine if r]
        if mine:
            groups.append(
                f'<div class="group {PLATFORM_CLASS.get(platform, "")}">'
                f'<p class="group-name">{escape(PLATFORM_NAME.get(platform, platform))}</p>{"".join(mine)}</div>'
            )
    return "".join(groups)


def _row_open(arm: dict[str, Any], tip: str) -> str:
    kind = " is-native" if _is_native(arm["arm"]) else ""
    return (
        f'<div class="row{kind}" title="{escape(tip)}" tabindex="0">'
        f'<span class="lab">{escape(_spec(arm["arm"]))}</span><span class="track">'
    )


def _accuracy_chart(arms: list[dict[str, Any]], platforms: list[str]) -> str:
    shown = [a for a in arms if a.get("accuracy") is not None and a.get("accuracy_ci95")]
    omitted = [a["arm"] for a in arms if a not in shown]
    if not shown:
        return _chart_frame("Accuracy", "No arm produced a gradable answer.", "", omitted)
    low = min(a["accuracy_ci95"][0] for a in shown)
    start = max(0.0, int(low / ACCURACY_TICK) * ACCURACY_TICK)
    span = 1.0 - start or 1.0

    def at(value: float) -> float:
        return (value - start) / span * 100

    def row(a: dict[str, Any]) -> str:
        lo, hi = a["accuracy_ci95"]
        tip = f"{a['arm']}: {a['accuracy']:.0%} ({a['correct']}/{a['served']}), 95% CI {lo:.0%}-{hi:.0%}"
        return (
            _row_open(a, tip)
            + f'<span class="whisker" style="left:{at(lo):.2f}%;width:{at(hi) - at(lo):.2f}%"></span>'
            + f'<span class="dot" style="left:{at(a["accuracy"]):.2f}%"></span>'
            + f'</span><span class="val">{a["accuracy"]:.0%}</span></div>'
        )

    ticks = []
    steps = round(span / ACCURACY_TICK)
    for i in range(steps + 1):
        value = start + i * ACCURACY_TICK
        ticks.append(f'<span style="left:{at(value):.2f}%">{value:.0%}</span>')
    scale = f'<div class="scale"><span class="lab"></span><span class="ticks">{"".join(ticks)}</span></div>'
    return _chart_frame(
        "Accuracy, with its 95% interval",
        "The dot is correct answers over served runs; the line is the Wilson interval. Overlapping lines are not "
        "told apart by this run.",
        _grouped(arms, platforms, lambda a: row(a) if a in shown else ""),
        omitted,
        scale,
    )


def _bar_chart(arms: list[dict[str, Any]], platforms: list[str], key: str, title: str, lede: str, fmt: Any) -> str:
    shown = [a for a in arms if a.get(key) is not None]
    omitted = [a["arm"] for a in arms if a not in shown]
    top = max((a[key] for a in shown), default=0) or 1

    def row(a: dict[str, Any]) -> str:
        return (
            _row_open(a, f"{a['arm']}: {fmt(a[key])}")
            + f'<span class="bar" style="width:{a[key] / top * 100:.2f}%"></span>'
            + f'</span><span class="val">{escape(fmt(a[key]))}</span></div>'
        )

    return _chart_frame(title, lede, _grouped(arms, platforms, lambda a: row(a) if a in shown else ""), omitted)


def _latency_chart(arms: list[dict[str, Any]], platforms: list[str]) -> str:
    shown = [a for a in arms if a.get("latency_p50") is not None and a.get("latency_p95") is not None]
    omitted = [a["arm"] for a in arms if a not in shown]
    top = max((a["latency_p95"] for a in shown), default=0) or 1

    def row(a: dict[str, Any]) -> str:
        p50, p95 = a["latency_p50"] / top * 100, a["latency_p95"] / top * 100
        tip = f"{a['arm']}: p50 {a['latency_p50']:.1f}s, p95 {a['latency_p95']:.1f}s"
        return (
            _row_open(a, tip)
            + f'<span class="span" style="left:{p50:.2f}%;width:{p95 - p50:.2f}%"></span>'
            + f'<span class="dot" style="left:{p50:.2f}%"></span>'
            + f'<span class="dot tail" style="left:{p95:.2f}%"></span>'
            + f'</span><span class="val">{a["latency_p50"]:.0f}s · {a["latency_p95"]:.0f}s</span></div>'
        )

    return _chart_frame(
        "Seconds per run: median and p95",
        "The filled dot is the median run, the ring the 95th-percentile run. A long line means a heavy tail.",
        _grouped(arms, platforms, lambda a: row(a) if a in shown else ""),
        omitted,
    )


def _results(arms: list[dict[str, Any]], both: bool) -> str:
    rows = []
    for a in arms:
        ci = a.get("accuracy_ci95")
        cells = [
            f'<th scope="row"><code>{escape(a["arm"])}</code></th>',
            *([f"<td>{escape(PLATFORM_NAME.get(a['platform'], a['platform']))}</td>"] if both else []),
            f'<td class="num">{_pct(a.get("accuracy"))} '
            f'<span class="muted">({a.get("correct")}/{a.get("served")})</span></td>',
            f'<td class="num">{f"{ci[0]:.0%}-{ci[1]:.0%}" if ci else "n/a"}</td>',
            f'<td class="num">{_pct(a.get("accuracy_when_attempted"))}</td>',
            f'<td class="num">{_pct(a.get("failure_rate"))}</td>',
            f'<td class="num">{_num(a.get("tool_calls"), 1)}</td>',
            f'<td class="num">{_num(a.get("input_tokens_p50"))}</td>',
            f'<td class="num">{_num(a.get("tool_chars_p50"))}</td>',
            f'<td class="num">{_usd(a.get("list_cost_p50"))}</td>',
            f'<td class="num">{_secs(a.get("latency_p50"))}</td>',
            f'<td class="num">{_secs(a.get("latency_p95"))}</td>',
        ]
        rows.append(f"<tr>{''.join(cells)}</tr>")
    head = (
        "<th>Arm</th>"
        + ("<th>Platform</th>" if both else "")
        + '<th class="num">Accuracy</th><th class="num">95% CI</th><th class="num">When attempted</th>'
        '<th class="num">Failed</th><th class="num">Tool calls</th><th class="num">Input tokens</th>'
        '<th class="num">Tool chars</th><th class="num">Anthropic list cost</th><th class="num">p50</th>'
        '<th class="num">p95</th>'
    )
    return (
        '<section><h2>Every arm</h2><p class="section-lede">Per served run. Tool calls are means; input tokens, tool '
        "chars and list cost are medians. n/a is a figure the platform does not report.</p>"
        f'<div class="scroll"><table class="results"><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div></section>"
    )


def _backends(setup: dict[str, Any]) -> str:
    from report import backend_lines  # imported here: report imports this module at its top

    backends = setup.get("backends") or {}
    if not backends:
        return ""
    rows = [
        f'<tr><th scope="row"><code>{escape(arm)}</code></th><td>{escape(tool)}</td><td>{escape(backend)}</td>'
        f'<td><code class="request">{escape(request)}</code></td></tr>'
        for arm, config in backends.items()
        for tool, backend, request in backend_lines(config)
    ]
    return (
        '<section><h2>What each backend was sent</h2><p class="section-lede">The request each web-router arm\'s '
        "backends received, with the model's query or URL as a placeholder. A field that is not listed was not "
        "sent, so the backend's own default decided it. Page reads go to the arm's own backend when it reads "
        "pages, else to <code>auto</code>, which tries its members in order.</p>"
        '<div class="scroll"><table class="results"><thead><tr><th>Arm</th><th>Tool</th><th>Backend</th>'
        f"<th>Request</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>"
    )


def _caveats(both: bool) -> str:
    items = [
        "<strong>Cost is Anthropic's side only.</strong> The list cost is what Anthropic reports for a Claude "
        "session: tokens, its own web searches and runtime. It leaves out what a web-router backend bills for its "
        "searches, and OpenAI reports no cost at all, so its arms show none.",
        "<strong>Accuracy is one judge's grade</strong> against the question set's gold answer. It compares only "
        "with runs graded by the same judge; every other figure is measured.",
        "<strong>Sample size decides what is settled.</strong> A gap marked not separated may close or widen on a "
        "larger run. Confirm a winner on fresh questions before acting on it.",
    ]
    if both:
        items.insert(
            1,
            "<strong>Compare within a platform.</strong> Claude and OpenAI arms run different models and count "
            "tokens with different tokenizers, so read each arm against its own platform's native search.",
        )
    return (
        '<section class="caveats"><h2>Reading these results</h2><ul>'
        + "".join(f"<li>{item}</li>" for item in items)
        + "</ul></section>"
    )


def _chip(verdict: str) -> str:
    return (
        f'<span class="chip {VERDICT_CLASS.get(verdict, "v-few")}">{escape(VERDICT_TEXT.get(verdict, verdict))}</span>'
    )


def _margin(entry: dict[str, Any], margin: dict[str, Any]) -> str:
    unit, value = entry["unit"], margin.get("margin")
    if value is None:
        return "n/a"
    if unit == "pp":
        return f"{value:+.1f} pp"
    if unit == "pct":
        return f"{value:+.0%}"
    if unit == "s":
        return f"{value:+.1f}s"
    return f"{value:+.1f}"


def _value(unit: str, axis: str, value: float) -> str:
    if unit == "pp":
        return f"{value:.0%}"
    if unit == "s":
        return f"{value:.1f}s"
    if axis.startswith("list_cost"):
        return f"${value:,.2f}"
    return f"{value:,.1f}" if unit == "abs" else f"{value:,.0f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _num(value: float | None, digits: int = 0) -> str:
    return "n/a" if value is None else f"{value:,.{digits}f}"


def _usd(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def _secs(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}s"


# Layout: one reading column; the headline as two platform cards, then tables and CSS-drawn charts, whose text
# stays at reading size on a phone where a scaled SVG's would shrink. Platform identity is the reference
# palette's first two categorical slots; verdicts are status chips with words, never color alone.
_STYLE = """<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:wght@500;700&family=Source+Sans+3:wght@400;600&family=JetBrains+Mono:wght@400;600&display=swap">
<style>
:root {
  --bg: #f7f8fa; --surface: #ffffff; --fg: #14171c; --muted: #5a6270; --rule: #dde1e7; --track: #eceff3;
  --warn: #a8471a;
  --claude: #2a78d6; --openai: #eb6834; --good: #1d7a45; --good-bg: #e3f3e9; --neutral-bg: #eef0f3;
  --display: "Schibsted Grotesk", "Helvetica Neue", Arial, sans-serif;
  --body: "Source Sans 3", "Segoe UI", system-ui, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, "SF Mono", Menlo, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #121418; --surface: #1a1d22; --fg: #eef0f3; --muted: #a3abb7; --rule: #2c3139; --track: #262a31;
    --warn: #f0a070;
    --claude: #3987e5; --openai: #d95926; --good: #6fd39a; --good-bg: #173524; --neutral-bg: #262a31;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #121418; --surface: #1a1d22; --fg: #eef0f3; --muted: #a3abb7; --rule: #2c3139; --track: #262a31;
  --warn: #f0a070;
  --claude: #3987e5; --openai: #d95926; --good: #6fd39a; --good-bg: #173524; --neutral-bg: #262a31;
  color-scheme: dark;
}
body { background: var(--bg); color: var(--fg); font: 16px/1.55 var(--body); }
.page { max-width: 1040px; margin: 0 auto; padding-inline: 16px; padding-block: 32px 56px;
  display: grid; gap: 40px; }
h1, h2, h3 { font-family: var(--display); text-wrap: balance; margin: 0; line-height: 1.2; }
h1 { font-size: clamp(1.6rem, 4vw, 2.3rem); font-weight: 700; }
h2 { font-size: 1.35rem; font-weight: 700; margin-bottom: 6px; }
h3 { font-size: 1.05rem; font-weight: 700; }
p { margin: 0; }
code { font-family: var(--mono); font-size: 0.85em; overflow-wrap: anywhere; }
section { display: grid; gap: 14px; min-width: 0; }
.eyebrow { font: 600 0.75rem/1 var(--mono); letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }
.intro { display: grid; gap: 14px; }
.lede, .section-lede { max-width: 68ch; color: var(--muted); }
.lede { font-size: 1.08rem; color: var(--fg); }
.note { max-width: 68ch; font-size: 0.92rem; color: var(--muted); border-left: 3px solid var(--rule);
  padding-left: 12px; }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px 24px; margin: 0; }
.facts div { min-width: 0; }
.facts dt { font: 600 0.72rem/1.4 var(--mono); letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--muted); }
.facts dd { margin: 2px 0 0; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
.card { background: var(--surface); border: 1px solid var(--rule); border-radius: 10px; padding: 18px 20px;
  display: grid; gap: 12px; min-width: 0; }
.card h3 { display: flex; align-items: center; gap: 8px; }
.card h3::before, .block h3::before { content: ""; width: 10px; height: 10px; border-radius: 50%;
  background: var(--series); }
.p-claude { --series: var(--claude); }
.p-openai { --series: var(--openai); }
.axes { list-style: none; margin: 0; padding: 0; display: grid; gap: 14px; }
.axes li { display: grid; grid-template-columns: 1fr auto; gap: 2px 12px; align-items: baseline;
  padding-top: 12px; border-top: 1px solid var(--rule); }
.axes li:first-child { border-top: 0; padding-top: 0; }
.axis { font: 600 0.75rem/1.4 var(--mono); letter-spacing: 0.05em; text-transform: uppercase; color: var(--muted); }
.figure { font: 700 1.25rem/1.2 var(--display); font-variant-numeric: tabular-nums; text-align: right; }
.detail, .none { grid-column: 1 / -1; font-size: 0.95rem; }
.none { color: var(--muted); }
.axes .chip { grid-column: 1 / -1; justify-self: start; }
.chip { display: inline-block; font: 600 0.72rem/1 var(--mono); letter-spacing: 0.04em; text-transform: uppercase;
  padding: 5px 8px; border-radius: 999px; white-space: nowrap; vertical-align: middle; }
.v-clear { background: var(--good-bg); color: var(--good); }
.v-clear::before { content: "✓ "; }
.v-noise, .v-few { background: var(--neutral-bg); color: var(--muted); }
.v-noise::before { content: "≈ "; }
.block { display: grid; gap: 8px; min-width: 0; }
.block h3 { display: flex; align-items: center; gap: 8px; }
.scroll { overflow-x: auto; min-width: 0; border: 1px solid var(--rule); border-radius: 10px;
  background: var(--surface); }
table { border-collapse: collapse; width: 100%; font-size: 0.92rem; }
th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--rule); vertical-align: top; }
thead th { font: 600 0.72rem/1.3 var(--mono); letter-spacing: 0.05em; text-transform: uppercase;
  color: var(--muted); white-space: nowrap; }
tbody tr:last-child th, tbody tr:last-child td { border-bottom: 0; }
tbody th { font-weight: 600; }
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.results td, .results th { white-space: nowrap; }
/* On a phone the table scrolls inside its frame rather than breaking arm names mid-word. */
.scroll table { min-width: 680px; }
td code, th code { white-space: nowrap; overflow-wrap: normal; }
td code.request { white-space: normal; overflow-wrap: anywhere; }
.alert { border: 2px solid var(--warn); border-radius: 12px; padding: 16px 20px; }
.alert h2 { color: var(--warn); }
.muted { color: var(--muted); }
.legend { display: flex; flex-wrap: wrap; gap: 8px 18px; font-size: 0.9rem; color: var(--muted); }
.key { display: inline-flex; align-items: center; gap: 6px; }
.key i { width: 11px; height: 11px; border-radius: 50%; background: var(--series); }
.key.native i { background: var(--surface); border: 2px solid var(--muted); width: 7px; height: 7px; }
.charts { display: grid; gap: 20px; }
.chart { margin: 0; background: var(--surface); border: 1px solid var(--rule); border-radius: 10px;
  padding: 18px 20px; display: grid; gap: 12px; min-width: 0; }
figcaption { display: grid; gap: 2px; }
figcaption strong { font-family: var(--display); font-size: 1.02rem; }
figcaption span { color: var(--muted); font-size: 0.92rem; max-width: 68ch; }
.group { display: grid; gap: 6px; }
.group-name { font: 600 0.72rem/1.4 var(--mono); letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--muted); margin-top: 4px; }
.row, .scale { display: grid; grid-template-columns: minmax(0, 15rem) minmax(0, 1fr) 6.5rem; gap: 12px;
  align-items: center; border-radius: 6px; }
.row:hover, .row:focus-visible { background: var(--track); outline: none; }
.row:focus-visible { box-shadow: 0 0 0 2px var(--series); }
.lab { font: 0.82rem/1.3 var(--mono); overflow-wrap: anywhere; }
.val { font: 600 0.85rem/1 var(--mono); font-variant-numeric: tabular-nums; text-align: right; }
.track { position: relative; height: 22px; background: linear-gradient(var(--track), var(--track)) center / 100% 2px
  no-repeat; }
.bar { position: absolute; left: 0; top: 5px; height: 12px; background: var(--series); border-radius: 0 4px 4px 0; }
.is-native .bar { background: var(--surface); border: 2px solid var(--series); height: 8px; }
.whisker, .span { position: absolute; top: 10px; height: 2px; background: var(--series); }
.dot { position: absolute; top: 5px; width: 12px; height: 12px; margin-left: -6px; border-radius: 50%;
  background: var(--series); box-shadow: 0 0 0 2px var(--surface); }
.is-native .dot { background: var(--surface); border: 2px solid var(--series); width: 8px; height: 8px; }
.dot.tail { background: var(--surface); border: 2px solid var(--series); width: 8px; height: 8px; }
.scale .ticks { position: relative; height: 16px; }
.scale .ticks span { position: absolute; transform: translateX(-50%); font: 0.7rem/1 var(--mono); color: var(--muted); }
.omitted { font-size: 0.85rem; color: var(--muted); }
.caveats ul { margin: 0; padding-left: 20px; display: grid; gap: 8px; max-width: 72ch; }
@media (max-width: 560px) {
  .row, .scale { grid-template-columns: minmax(0, 1fr) 5.5rem; }
  .row .lab { grid-column: 1 / -1; }
  .row .track { grid-column: 1; }
  .scale .lab { display: none; }
  .card, .chart { padding: 16px; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>"""


def write(folder: Path, summary: dict[str, Any]) -> Path:
    """Write ``page.html`` beside the summary; return its path."""
    path = folder / PAGE_FILE
    path.write_text(render(summary), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> None:
    folder = Path((argv if argv is not None else sys.argv[1:])[0])
    summary = json.loads((folder / SUMMARY_FILE).read_text(encoding="utf-8"))
    print(f"wrote {write(folder, summary)}")


if __name__ == "__main__":
    main()
