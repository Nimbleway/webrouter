"""router-latency's routers: each one named on the command line, how it is built, what it needs and what it costs.

``rules`` is the library's built-in router, the one the MCP server attaches.
``sklearn``, ``llm`` and ``jev`` are the example routers in ``examples/``,
loaded from their files as route-eval loads a ``path/file.py:attr`` router.
``remote:<url>`` is the library's own ``remote`` router, posting each request
to a router service in the wire format, schema 1. Any other ``module:attr`` or ``path/file.py:attr``
names a router of your own. Imported as a sibling, by name, as ``_common`` is.

Experimental until 1.0, like the router contract it measures.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import math
import os
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

from web_router._urls import host_of
from web_router.constants_remote import ENV_REMOTE_ROUTER_API_KEY
from web_router.routing.contract import RouteDecision, RouteRequest
from web_router.routing.registry import aclose_router, discard_router
from web_router.routing.remote import RemoteRouter

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

RULES, SKLEARN, LLM, JEV, REMOTE = "rules", "sklearn", "llm", "jev", "remote:"
KNOWN = (RULES, SKLEARN, LLM, JEV, REMOTE + "<url>")

LLM_KEY_ENV, LLM_MODEL_ENV, LLM_BASE_URL_ENV = "OPENAI_API_KEY", "LLM_ROUTER_MODEL", "OPENAI_BASE_URL"
JEV_KEY_ENV = "TYPESAFE_API_KEY"
HANDSHAKE_RTTS = 3
"""Round trips a new connection's first call pays on a simulated link: TCP, TLS 1.3, then the request."""

CHARS_PER_TOKEN = 4
"""A rough English-text token size, for the dry run's estimate only."""
REASON_TOKENS = 20
"""What a model's one-line reason adds to its answer, for the estimate."""
MTOK = 1_000_000

MSG_BAD_ROUTER = "{spec!r}: a router is one of {known}, or module:attr / path/file.py:attr"
MSG_NO_SKLEARN = "the sklearn router needs scikit-learn: uv run --all-extras --with scikit-learn python ..."
MSG_LINK_TIMEOUT = "the simulated round trip took the whole deadline"


@dataclass(frozen=True)
class RouterSpec:
    """One router to time: its name, how to build it, the variables it reads, and its cost per call."""

    name: str
    describe: str
    build: Callable[[], Any]
    env: tuple[str, ...] = ()
    optional_env: tuple[str, ...] = ()
    cost_usd: Callable[[RouteRequest], float | None] | None = None
    """The estimated cost of one call; ``None`` from it when unknown. Absent for a free router."""
    cost_note: str = "free: runs in this process"

    def missing_env(self) -> list[str]:
        """The variables it needs that are unset: names only, never a value."""
        return [name for name in self.env if not os.environ.get(name)]


def load_file(path: Path) -> ModuleType:
    """A Python file loaded as a module, as route-eval loads ``path/file.py:attr``."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise SystemExit(MSG_BAD_ROUTER.format(spec=str(path), known=", ".join(KNOWN)))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # registered while it runs: a dataclass looks its module up by name
    spec.loader.exec_module(module)
    return module


@cache
def example(name: str) -> ModuleType:
    """One of ``examples/``, by file name, loaded once."""
    return load_file(EXAMPLES / name)


def _rules() -> Any:
    from web_router.routing.rules import rules

    return rules


def _sklearn() -> Any:
    """The example classifier, trained on its toy set: the time is predict_proba's, which a real model shares."""
    try:
        import joblib
    except ImportError:
        raise SystemExit(MSG_NO_SKLEARN) from None
    classifier = example("router_classifier.py")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "router.joblib"
        try:
            classifier.train(path)
        except ImportError:
            raise SystemExit(MSG_NO_SKLEARN) from None
        return classifier.ClassifierRouter(joblib.load(path))


class DelayedRouter:
    """A router behind a simulated link: ``rtt_ms`` slept before each call, :data:`HANDSHAKE_RTTS` times on the first.

    It stands in for a router service on another host when none is at hand; the
    results file says the delay was simulated.
    """

    def __init__(self, inner: Any, rtt_ms: float) -> None:
        self.inner = inner
        self.rtt_s = rtt_ms / 1000
        self.timeout_s = inner.timeout_s
        self._first = True

    async def aroute(self, req: RouteRequest) -> RouteDecision:
        slept = self.rtt_s * (HANDSHAKE_RTTS if self._first else 1)
        self._first = False
        if slept >= req.deadline_s:
            await asyncio.sleep(req.deadline_s)
            raise TimeoutError(MSG_LINK_TIMEOUT)
        await asyncio.sleep(slept)
        return await self.inner.aroute(replace(req, deadline_s=req.deadline_s - slept))  # type: ignore[no-any-return]

    def close(self) -> None:
        close(self.inner)

    async def aclose(self) -> None:
        await aclose(self.inner)


def llm_tokens(req: RouteRequest) -> tuple[int, int]:
    """Estimated input and output tokens of one LLM router call: its instructions, menu and schema; a full ranking."""
    llm = example("router_llm.py")
    sent = llm.INSTRUCTIONS + llm.menu(req) + json.dumps(llm.schema(req))
    ranking = json.dumps({"ranked": [c.id for c in llm.usable(req)], "reason": ""})
    return math.ceil(len(sent) / CHARS_PER_TOKEN), math.ceil(len(ranking) / CHARS_PER_TOKEN) + REASON_TOKENS


def _llm(model: str | None, price: tuple[float, float] | None) -> RouterSpec:
    def build() -> Any:
        return example("router_llm.py").LLMRouter(model=model)

    def cost(req: RouteRequest) -> float | None:
        if price is None:
            return None
        tokens_in, tokens_out = llm_tokens(req)
        return (tokens_in * price[0] + tokens_out * price[1]) / MTOK

    shown = model or f"${LLM_MODEL_ENV}"
    note = (
        f"{price[0]:g}/{price[1]:g} USD per 1M input/output tokens (--llm-price), tokens estimated at "
        f"{CHARS_PER_TOKEN} characters each; the stubbed searches priced as sampled requests, an approximation"
        if price
        else "unknown: pass --llm-price IN,OUT (USD per 1M tokens) for the model"
    )
    env = (LLM_KEY_ENV,) if model else (LLM_KEY_ENV, LLM_MODEL_ENV)
    return RouterSpec(
        LLM, f"examples/router_llm.py LLMRouter, model {shown}", build, env, (LLM_BASE_URL_ENV,), cost, note
    )


def _jev(per_call: float | None) -> RouterSpec:
    def build() -> Any:
        return example("router_jev.py").JevRouter()

    note = f"{per_call:g} USD per call (--jev-price)" if per_call is not None else "unknown: pass --jev-price"
    model = example("router_jev.py").MODEL
    return RouterSpec(
        JEV, f"examples/router_jev.py JevRouter, model {model}", build, (JEV_KEY_ENV,), (), lambda _: per_call, note
    )


def _custom(spec: str) -> RouterSpec:
    target, sep, attr = spec.rpartition(":")
    if not sep or not target or not attr:
        raise SystemExit(MSG_BAD_ROUTER.format(spec=spec, known=", ".join(KNOWN)))

    def build() -> Any:
        module = load_file(Path(target)) if target.endswith(".py") else importlib.import_module(target)
        return getattr(module, attr)

    return RouterSpec(
        spec, f"{attr} from {target}", build, cost_usd=lambda _: None, cost_note="unknown: a router of your own"
    )


def _remote(spec: str, rtt_ms: float) -> RouterSpec:
    url = spec.removeprefix(REMOTE)
    shown = REMOTE + host_of(url)  # never the path, query or credentials: they may carry a token
    if not rtt_ms:
        name, build = shown, lambda: RemoteRouter(url=url)
        link = "as the URL lies (loopback: no network)"
    else:
        name, build = f"{shown} +{rtt_ms:g}ms", lambda: DelayedRouter(RemoteRouter(url=url), rtt_ms)
        link = f"plus a simulated {rtt_ms:g} ms RTT ({HANDSHAKE_RTTS} RTTs on a new connection)"
    return RouterSpec(
        name,
        f"web_router.routing.remote.RemoteRouter at {host_of(url)}, link {link}",
        build,
        optional_env=(ENV_REMOTE_ROUTER_API_KEY,),
        cost_note="free to the caller: the service's own cost is not counted",
    )


def router_specs(
    spec: str,
    *,
    llm_model: str | None = None,
    llm_price: tuple[float, float] | None = None,
    jev_price: float | None = None,
    rtts_ms: Sequence[float] = (),
) -> list[RouterSpec]:
    """The routers a command-line name stands for: one, or for ``remote:<url>`` one per stated RTT and loopback."""
    if spec.startswith(REMOTE):
        return [_remote(spec, rtt) for rtt in (0.0, *rtts_ms)]
    return [router_spec(spec, llm_model=llm_model, llm_price=llm_price, jev_price=jev_price)]


def router_spec(
    spec: str,
    *,
    llm_model: str | None = None,
    llm_price: tuple[float, float] | None = None,
    jev_price: float | None = None,
) -> RouterSpec:
    """The router a command-line name stands for. Nothing is built, and nothing called, until ``build()``."""
    if spec == RULES:
        return RouterSpec(RULES, "web_router.routing.rules.rules (built in; the MCP server's default)", _rules)
    if spec == SKLEARN:
        return RouterSpec(SKLEARN, "examples/router_classifier.py ClassifierRouter, toy-trained", _sklearn)
    if spec == LLM:
        return _llm(llm_model, llm_price)
    if spec == JEV:
        return _jev(jev_price)
    if spec.startswith(REMOTE):
        return _remote(spec, 0.0)
    return _custom(spec)


def close(router: Any) -> None:
    """Close what a built router holds open: its ``close()``, else its ``aclose()``, as the library does."""
    discard_router(router)


async def aclose(router: Any) -> None:
    """:func:`close` on the event loop that ran the router's calls: its ``aclose()``, else its ``close()``."""
    await aclose_router(router)


def plan_cost(spec: RouterSpec, requests: Sequence[RouteRequest]) -> float | None:
    """The estimated cost of calling ``spec`` once per request; ``0`` for a free router, ``None`` when unknown."""
    if spec.cost_usd is None:
        return 0.0
    costs = [spec.cost_usd(req) for req in requests]
    return None if any(c is None for c in costs) else sum(c for c in costs if c is not None)
