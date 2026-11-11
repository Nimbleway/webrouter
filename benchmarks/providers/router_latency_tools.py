"""router-latency's yardstick: each routable tool's p50 and p90, and the rule that weighs a router against them.

**A tool's p50 is the p50 of the first backend in its default ``auto`` chain**,
read from the price list's measured latency (``prices.json``, ``make
latency``): the backend a call with no router runs first, so the call a router
would reorder. The chain is ``auto``'s with a key for every backend
(``--chain all``, route-eval's assumption), or with the keys this machine
holds (``--chain here``). Only search and answer have measured latency; for
extract, research and browse the head's latency is not measured, so their
verdict is a judgement call, never a number made up here. Imported as a
sibling, by name, as ``_common`` is.

Experimental until 1.0, like the router contract it measures.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from web_router import prices
from web_router.constants import (
    AUTO_APPENDED,
    AUTO_BROWSE_PREFERRED,
    AUTO_EXTRACT_APPENDED,
    AUTO_EXTRACT_PREFERRED,
    AUTO_PREFERRED,
    AUTO_RESEARCH_PREFERRED,
)
from web_router.constants_answer import AUTO_ANSWER_PREFERRED
from web_router.providers.auto import AutoBrowseProvider, AutoExtractProvider, AutoProvider, AutoResearchProvider
from web_router.providers.auto_answer import AutoAnswerProvider

TOOLS = ("search", "extract", "answer", "research", "browse")
"""The tools ``route_tools`` names, in the README's order."""

SHARE = 0.25
"""The starting rule: a tool asks the router for its provider order only when the router's p50 is below this
share of the tool's p50, so ordering the backends never costs more than a quarter of the call it orders."""

CHAIN_ALL, CHAIN_HERE = "all", "here"
CHAINS = (CHAIN_ALL, CHAIN_HERE)

CONSULT = "consult"
SKIP = "skip"
JUDGEMENT = "judgement, not measured"
UNSURE = "unsure: router p50 censored"

DEFINITION = (
    "A tool's p50 and p90 are those of the first backend in its default auto chain ({chain}), from the price "
    "list's measured latency (prices.json, make latency). Search and answer only are measured; extract, research "
    "and browse are a judgement call. The rule: a tool consults the router for its provider order only when the "
    "router's p50 is below {share:.0%} of the tool's p50."
)
CHAIN_WORDS = {CHAIN_ALL: "a key for every backend", CHAIN_HERE: "the keys this machine holds"}


@dataclass(frozen=True)
class ToolLatency:
    """One tool's yardstick: the head of its ``auto`` chain, that backend's latency, and where it comes from."""

    tool: str
    head: str | None
    p50_s: float | None
    p90_s: float | None
    calls: int | None
    measured: str | None
    """The date of the newest run pooled, as the price list says."""
    range_p50_s: tuple[float, float] | None
    """The lowest and highest p50 across the chain's measured backends: how much the head's choice moves the rule."""

    @property
    def is_measured(self) -> bool:
        return self.p50_s is not None

    def summary(self) -> dict[str, Any]:
        return asdict(self)


def chain_orders(chain: str) -> dict[str, tuple[str, ...]]:
    """Each tool's default ``auto`` order: with a key for every backend, or with the keys held here."""
    every = chain == CHAIN_ALL
    built: dict[str, tuple[Callable[..., Any], tuple[str, ...]]] = {
        "search": (AutoProvider.default_chain, (*AUTO_PREFERRED, *AUTO_APPENDED)),
        "extract": (AutoExtractProvider.default_chain, (*AUTO_EXTRACT_PREFERRED, *AUTO_EXTRACT_APPENDED)),
        "answer": (AutoAnswerProvider.default_chain, tuple(AUTO_ANSWER_PREFERRED)),
        "research": (AutoResearchProvider.default_chain, tuple(AUTO_RESEARCH_PREFERRED)),
        "browse": (AutoBrowseProvider.default_chain, tuple(AUTO_BROWSE_PREFERRED)),
    }
    return {
        tool: tuple(name for name, _ in default_chain(frozenset(names) if every else frozenset()))
        for tool, (default_chain, names) in built.items()
    }


def _yardstick(tool: str, order: tuple[str, ...]) -> ToolLatency:
    head = order[0] if order else None
    row = None if head is None else prices.price(head, tool)
    p50s = [p[0] for name in order if (p := prices.estimate_latency(name, tool)) is not None]
    spread = (min(p50s), max(p50s)) if p50s else None
    if row is None or row.latency_p50_s is None:
        return ToolLatency(tool, head, None, None, None, None, spread)
    measured = None if row.latency_measured is None else row.latency_measured.isoformat()
    return ToolLatency(tool, head, row.latency_p50_s, row.latency_p90_s, row.latency_n, measured, spread)


def tool_latencies(chain: str = CHAIN_ALL) -> dict[str, ToolLatency]:
    """Every routable tool's yardstick on ``chain``'s orders."""
    orders = chain_orders(chain)
    return {tool: _yardstick(tool, orders.get(tool, ())) for tool in TOOLS}


def verdict(router_p50_s: float | None, tool: ToolLatency, share: float = SHARE) -> tuple[str, float | None]:
    """``consult`` when the router's p50 is under ``share`` of the tool's, else ``skip``; and the ratio.

    A tool with no measured p50 is a judgement call; a router whose p50 is censored (half its calls or more
    timed out) has no p50 to weigh.
    """
    if not tool.p50_s:
        return JUDGEMENT, None
    if router_p50_s is None:
        return UNSURE, None
    ratio = router_p50_s / tool.p50_s
    return (CONSULT if ratio < share else SKIP), ratio


def route_tools_for(router_p50_s: float | None, tools: Mapping[str, ToolLatency], share: float = SHARE) -> list[str]:
    """The measured tools on which the rule would have this router asked."""
    return [name for name in TOOLS if verdict(router_p50_s, tools[name], share)[0] == CONSULT]


def definition(chain: str, share: float = SHARE) -> str:
    return DEFINITION.format(chain=CHAIN_WORDS[chain], share=share)
