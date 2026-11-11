"""router-latency's command line: its options, the checks each value passes, the routers and settings it names.

Imported as a sibling, by name, as ``_common`` is. Experimental until 1.0.
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from frozen import JOINED_DIR
from router_latency_routers import RouterSpec, router_specs
from router_latency_tools import CHAIN_ALL, CHAINS, SHARE

DEFAULT_SETS = ("tools-effort-60", "simpleqa-300-v2")
"""route-eval's public prompts: the mixed tool set, and SimpleQA's search questions."""
DEFAULT_ROUTERS = ("rules", "sklearn")
"""The routers that run in this process and bill nothing."""
DEFAULT_N = 200
DEFAULT_WARMUP = 5
DEFAULT_COLD = 20
DEFAULT_E2E = 100
DEFAULT_SEED = 0

MSG_COUNT = "a count of calls is 0 or more; got {value}"
MSG_POSITIVE = "--e2e is 1 or more: the added wall time needs a timed call; got {value}"
MSG_RTT = "--remote-rtt-ms is a finite number of milliseconds, 0 or more; got {value!r}"
MSG_SECONDS = "--deadline-s is a finite number of seconds above 0; got {value!r}"
MSG_SHARE = "--share is a fraction above 0 and at most 1; got {value!r}"
MSG_USD = "a price is a finite number of USD, 0 or more; got {value!r}"
MSG_REPEATED = "each router is timed once per run; named more than once: {names}"
MSG_PRICE = "--llm-price takes IN,OUT: USD per million input and output tokens, as 0.15,0.60"


def _count(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(MSG_COUNT.format(value=value))
    return value


def _positive(text: str) -> int:
    value = _count(text)
    if value < 1:
        raise argparse.ArgumentTypeError(MSG_POSITIVE.format(value=value))
    return value


def _finite(text: str, message: str, *, low: float, inclusive: bool = True, high: float = math.inf) -> float:
    """``text`` as a finite number from ``low`` (``inclusive`` or not) to ``high``; else ``message`` refuses it."""
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value < low or (value == low and not inclusive) or value > high:
        raise argparse.ArgumentTypeError(message.format(value=text))
    return value


def _rtt(text: str) -> float:
    """A simulated round trip: a finite number of milliseconds, 0 or more."""
    return _finite(text, MSG_RTT, low=0)


def _seconds(text: str) -> float:
    """A deadline: a finite number of seconds above 0."""
    return _finite(text, MSG_SECONDS, low=0, inclusive=False)


def _share(text: str) -> float:
    """The rule's share of a tool's p50: above 0, at most 1."""
    return _finite(text, MSG_SHARE, low=0, inclusive=False, high=1)


def _usd_amount(text: str) -> float:
    """A price: a finite number of USD, 0 or more."""
    return _finite(text, MSG_USD, low=0)


def _price(text: str) -> tuple[float, float]:
    parts = text.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(MSG_PRICE)
    try:
        return _usd_amount(parts[0]), _usd_amount(parts[1])
    except argparse.ArgumentTypeError:
        raise argparse.ArgumentTypeError(MSG_PRICE) from None


def parse_args(argv: Sequence[str], *, description: str) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--router", dest="routers", action="append", help=f"rules, sklearn, llm, jev, remote:<url>, "
                   f"module:attr or path/file.py:attr (default: {', '.join(DEFAULT_ROUTERS)})")  # fmt: skip
    p.add_argument("--set", dest="sets", action="append", help=f"frozen set (default: {', '.join(DEFAULT_SETS)})")
    p.add_argument("--joined", type=Path, default=JOINED_DIR, help="frozen.py join's output, the question text")
    p.add_argument("-n", type=_count, default=DEFAULT_N, help="timed calls per router, after the warm-up")
    p.add_argument("--warmup", type=_count, default=DEFAULT_WARMUP, help="calls dropped after the first")
    p.add_argument("--cold", type=_count, default=DEFAULT_COLD, help="calls each on a freshly built router")
    p.add_argument("--e2e", type=_positive, default=DEFAULT_E2E, help="stubbed searches, routed against unrouted")
    p.add_argument("--deadline-s", type=_seconds, default=None, help="caps each router's deadline (not --e2e's)")
    p.add_argument("--async", dest="use_async", action="store_true", help="call from an event loop, as acall")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED, help="the prompt sample's and call order's seed")
    p.add_argument("--chain", choices=CHAINS, default=CHAIN_ALL, help="auto's order: every key, or the keys here")
    p.add_argument("--share", type=_share, default=SHARE, help="the rule's share of the tool's p50")
    p.add_argument("--llm-model", default=None, help="the llm router's model (default: $LLM_ROUTER_MODEL)")
    p.add_argument("--llm-price", type=_price, default=None, help="IN,OUT USD per million tokens, for the estimate")
    p.add_argument("--jev-price", type=_usd_amount, default=None, help="USD per Jev call, for the estimate")
    p.add_argument("--remote-rtt-ms", type=_rtt, action="append", default=[], help="also time remote over this RTT")
    p.add_argument("--out", type=Path, default=None, help="results file (default: results/router-latency/<time>)")
    p.add_argument("--dry-run", action="store_true", help="print what would run and its cost; call nothing")
    return p.parse_args(argv)


def router_specs_for(args: argparse.Namespace) -> list[RouterSpec]:
    """The routers the command line names, each built only when timed; one named twice is refused."""
    specs: list[RouterSpec] = []
    for spec in args.routers or DEFAULT_ROUTERS:
        specs += router_specs(
            spec,
            llm_model=args.llm_model,
            llm_price=args.llm_price,
            jev_price=args.jev_price,
            rtts_ms=args.remote_rtt_ms,
        )
    names = [s.name for s in specs]
    if len(set(names)) != len(names):
        raise SystemExit(MSG_REPEATED.format(names=", ".join(sorted({n for n in names if names.count(n) > 1}))))
    return specs


def settings(args: argparse.Namespace) -> dict[str, Any]:
    """The run's settings as the results file records them."""
    return {
        "sets": list(args.sets or DEFAULT_SETS),
        "n": args.n,
        "warmup": args.warmup,
        "cold": args.cold,
        "e2e": args.e2e,
        "deadline_s": args.deadline_s,
        "async": args.use_async,
        "seed": args.seed,
        "chain": args.chain,
        "share": args.share,
    }
