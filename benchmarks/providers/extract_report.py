"""The extract scan's report: ``report.md``, one table at a time, from the numbers ``summary.json`` holds."""

from __future__ import annotations

import json
from typing import Any

from _common import APART, MIN_GROUP, _p, _s

from web_router.constants import PROVIDER_AUTO


def recommended_order(summary: dict[str, Any]) -> list[tuple[str, bool | None]]:
    """The backends by pages brought back with text, out of every URL asked, each with whether this run shows it behind.

    Ranked on pages with text over every URL, a failure or a timeout counting
    as a miss, as the search scan ranks on correct answers over every
    question. ``auto`` is left out if a scan named it: as a chain member it
    would be a second ``auto`` on the default order. So is a backend that
    returned nothing at all. A backend is behind only when its 95% interval
    stops short of the best one's; ``None`` marks the best itself.
    """
    overall = summary["overall"]
    plain = [p for p in summary["setup"]["providers"] if p != PROVIDER_AUTO and overall[p]["returned"]]
    ranked = sorted(plain, key=lambda p: -(overall[p]["with_text_rate"] or 0.0))
    if not ranked:
        return []
    floor = (overall[ranked[0]]["with_text_ci95"] or (0.0, 0.0))[0]
    return [(ranked[0], None)] + [(p, (overall[p]["with_text_ci95"] or (0.0, 0.0))[1] < floor) for p in ranked[1:]]


def markdown(summary: dict[str, Any]) -> str:
    """The report: the overall table, the suggested order, the category table and how to read them."""
    setup, overall = summary["setup"], summary["overall"]
    providers = sorted(setup["providers"], key=lambda p: -(overall[p]["with_text_rate"] or 0))
    lines = _header(setup) + _overall_table(overall, providers) + _recommendation(summary)
    lines += _category_table(summary["by_category"], providers) + _how_to_read()
    return "\n".join(lines) + "\n"


def _header(setup: dict[str, Any]) -> list[str]:
    sent = f"`format={setup['format']}`" if setup.get("format") else "no format named (markdown where a backend can)"
    return [
        f"# Extract backends on {setup['urls_title']}: pages returned by backend and by category\n",
        f"{setup.get('asked', setup['limit'])} URLs of {setup['urls_title']} (seed {setup['seed']}), one extract per "
        f"backend with {sent} and {setup['timeout']:g}s for each URL's comparison. A page is *returned* when the call "
        "answered with a document, and *with text* when that document had any content; nothing judges the text. "
        f"Started {setup['started']}.\n",
    ]


def _overall_table(overall: dict[str, Any], providers: list[str]) -> list[str]:
    lines = [
        "## Overall\n",
        "| Backend | Pages with text | 95% CI | Empty | Failed | Timed out | Refused | Not configured "
        "| p50 latency | p95 latency | Median chars |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for p in providers:
        s = overall[p]
        ci = s["with_text_ci95"]
        lines.append(
            f"| `{p}` | {_p(s['with_text_rate'])} ({s['with_text']}/{s['urls']}) "
            f"| {_p(ci[0]) + '-' + _p(ci[1]) if ci else 'n/a'} | {s['empty']} | {s['failed']} | {s['timed_out']} "
            f"| {s['refused']} | {s.get('not_configured', 0)} | {_s(s['latency_p50'])} | {_s(s['latency_p95'])} "
            f"| {_k(s['chars_median'])} |"
        )
    lines.append("")
    lines.append(
        "*Empty* is a call that answered with no text -- an empty document, or none; *failed* a call the backend "
        "refused or could not complete; *timed out* a call still unanswered when the comparison's timeout ran out; "
        "*refused* a format the backend cannot return; *not configured* a backend that was never built here -- no "
        "key or no extra -- so no call was made and nothing is known about it. Latency is over the calls that "
        "answered.\n"
    )
    return lines


def _recommendation(summary: dict[str, Any]) -> list[str]:
    order = recommended_order(summary)
    lines = ["## Recommended auto order\n"]
    if not order:
        lines.append("No backend in this scan brought a page back, so there is no order to suggest.\n")
        return lines
    overall = summary["overall"]
    lines.append(
        f"A suggestion from one scan of {summary['setup']['urls_title']}, not the library's default: the backends by "
        "pages brought back with text out of every URL asked, a failure or a timeout counting as a miss. It says "
        "which backend returns a page, not whose page is best. A backend is *behind* only when its 95% interval "
        "stops short of the best one's; the others are not shown apart by this run, and another could order them "
        "differently.\n"
    )
    lines.append(
        "| Order | Backend | Pages with text of all URLs | 95% CI | Empty | Failed | Timed out | Against the best |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for rank, (p, behind) in enumerate(order, 1):
        s = overall[p]
        ci = s["with_text_ci95"] or (0.0, 0.0)
        lines.append(
            f"| {rank} | `{p}` | {_p(s['with_text_rate'])} ({s['with_text']}/{s['urls']}) | {_p(ci[0])}-{_p(ci[1])} "
            f"| {s['empty']} | {s['failed']} | {s['timed_out']} | {APART[behind]} |"
        )
    chain = json.dumps({"auto": {"chain": {"extract": [p for p, _ in order]}}})
    lines.append(
        "\nIt is opt-in: to try it, give your own router the order. The chain replaces the default one, keyless "
        "tail included, and each member still needs its credentials; keyed by `extract`, it leaves the other "
        "methods' chains alone.\n"
    )
    lines.append(f"```python\nWebRouter(provider_config={chain})\n```\n")
    lines.append("The library's default order changes only by a pull request backed by a scan like this one.\n")
    return lines


def _category_table(by_category: dict[str, dict[str, Any]], providers: list[str]) -> list[str]:
    lines = [
        "## By category\n",
        f"Pages with text per backend over the URLs in the category; **bold** marks the most pages brought back with "
        f"text in the row. Rows under {MIN_GROUP} URLs name no best -- too few to tell backends apart.\n",
        "| Category | URLs | " + " | ".join(f"`{p}`" for p in providers) + " |",
        "|---|---|" + "---|" * len(providers),
    ]
    for group, entry in by_category.items():
        cells = []
        for p in providers:
            cell = _p(entry["with_text_rate"].get(p))
            cells.append(f"**{cell}**" if p in entry["leaders"] else cell)
        lines.append(f"| {group} | {entry['urls']} | " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def _how_to_read() -> list[str]:
    return [
        "## How to read this\n",
        "- **A returned page is not a correct page.** A backend that answers with a bot wall, a cookie banner or a "
        "login page has returned a document with text; this scan counts it. Judging the text against the page is "
        "work for a scorer with ground truth, which this scan does not carry.\n"
        "- **Intervals are wide.** Two backends whose 95% intervals overlap are not shown apart by this run; a "
        "category row with a few dozen URLs is wider still.\n"
        "- **Latency is around one call for one URL**, through the public `extract` path, with every backend "
        "called at once. A backend's own rate limit shows up here as latency or as failures.\n"
        "- **No cost column.** No backend states a price in its response; read these against `PRICING.md`.",
    ]


def _k(value: float | None) -> str:
    return "n/a" if value is None else f"{value / 1000:.1f}k"
