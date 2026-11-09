"""router-latency's routers: each one named on the command line, how it is built, what it needs and what it costs.

``rules`` is the library's built-in router, the one the MCP server attaches.
``sklearn``, ``llm`` and ``jev`` are the example routers in ``examples/``,
loaded from their files as route-eval loads a ``path/file.py:attr`` router.
``remote:<url>`` posts each request to a router service in the wire format,
schema 1: through the library's own remote router when the installed
``web_router`` has one, else through :class:`WireRouter` here, which speaks the
same format to any server. Any other ``module:attr`` or ``path/file.py:attr``
names a router of your own. Imported as a sibling, by name, as ``_common`` is.

Experimental until 1.0, like the router contract it measures.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import math
import os
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic import TypeAdapter

from web_router._urls import host_of
from web_router.routing.contract import RouteDecision, RouteRequest

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

RULES, SKLEARN, LLM, JEV, REMOTE = "rules", "sklearn", "llm", "jev", "remote:"
KNOWN = (RULES, SKLEARN, LLM, JEV, REMOTE + "<url>")

LLM_KEY_ENV, LLM_MODEL_ENV, LLM_BASE_URL_ENV = "OPENAI_API_KEY", "LLM_ROUTER_MODEL", "OPENAI_BASE_URL"
JEV_KEY_ENV = "TYPESAFE_API_KEY"
REMOTE_KEY_ENV = "WEB_ROUTER_REMOTE_ROUTER_API_KEY"
"""The variable the library's remote router reads its bearer key from; this script's fallback reads it too."""
REMOTE_MODULE, REMOTE_CLASS = "web_router.routing.remote", "RemoteRouter"
"""The library's remote router, used when the installed ``web_router`` ships it."""
WIRE_SCHEMA_KEY, WIRE_SCHEMA_VERSION = "schema", 1
REMOTE_TIMEOUT_S = 1.0
HTTP_OK = 200
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
MSG_WIRE = "the router service answered {why}"


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


class WireRouter:
    """A router service asked over HTTP in the wire format, schema 1, for a ``web_router`` without its own.

    One POST per call on one kept-alive connection, no redirect followed; the
    bearer key comes from ``WEB_ROUTER_REMOTE_ROUTER_API_KEY`` when set. A
    status other than 200, or an answer of another schema, is a router error.
    """

    timeout_s = REMOTE_TIMEOUT_S

    def __init__(self, url: str, transport: Any = None) -> None:
        """``transport``: an httpx transport in place of the network, for tests."""
        import httpx

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if key := os.environ.get(REMOTE_KEY_ENV):
            headers["Authorization"] = f"Bearer {key}"
        self.url = url
        self._client = httpx.Client(headers=headers, follow_redirects=False, transport=transport)
        self._request = TypeAdapter(RouteRequest)
        self._decision = TypeAdapter(RouteDecision)

    def route(self, req: RouteRequest) -> RouteDecision:
        body = {WIRE_SCHEMA_KEY: WIRE_SCHEMA_VERSION, **self._request.dump_python(req, mode="json")}
        response = self._client.post(self.url, json=body, timeout=req.deadline_s)
        if response.status_code != HTTP_OK:
            raise ValueError(MSG_WIRE.format(why=f"HTTP {response.status_code}"))
        data = response.json()
        if not isinstance(data, dict) or data.get(WIRE_SCHEMA_KEY) != WIRE_SCHEMA_VERSION:
            raise ValueError(MSG_WIRE.format(why="something other than a schema-1 decision"))
        payload = {k: v for k, v in data.items() if k != WIRE_SCHEMA_KEY and v is not None}
        return self._decision.validate_python(payload)

    def close(self) -> None:
        self._client.close()


def remote_router(url: str) -> Any:
    """The library's remote router when installed, else :class:`WireRouter`; either speaks schema 1 to ``url``."""
    if importlib.util.find_spec(REMOTE_MODULE) is not None:
        library = importlib.import_module(REMOTE_MODULE)
        return getattr(library, REMOTE_CLASS)(url=url, timeout_s=REMOTE_TIMEOUT_S)
    return WireRouter(url)


class DelayedRouter:
    """A router behind a simulated link: ``rtt_ms`` slept before each call, :data:`HANDSHAKE_RTTS` times on the first.

    It stands in for a router service on another host when none is at hand; the
    results file says the delay was simulated.
    """

    def __init__(self, inner: Any, rtt_ms: float) -> None:
        self.inner = inner
        self.rtt_s = rtt_ms / 1000
        self.timeout_s = getattr(inner, "timeout_s", REMOTE_TIMEOUT_S)
        self._first = True

    def route(self, req: RouteRequest) -> RouteDecision:
        slept = self.rtt_s * (HANDSHAKE_RTTS if self._first else 1)
        self._first = False
        if slept >= req.deadline_s:
            time.sleep(req.deadline_s)
            raise TimeoutError(MSG_LINK_TIMEOUT)
        time.sleep(slept)
        return self.inner.route(replace(req, deadline_s=req.deadline_s - slept))  # type: ignore[no-any-return]

    def close(self) -> None:
        close(self.inner)


def remote_client() -> str:
    """Which client ``remote:<url>`` runs on here, for the results file."""
    return "library" if importlib.util.find_spec(REMOTE_MODULE) is not None else "script (wire schema 1)"


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
        name, build = shown, lambda: remote_router(url)
        link = "as the URL lies (loopback: no network)"
    else:
        name, build = f"{shown} +{rtt_ms:g}ms", lambda: DelayedRouter(remote_router(url), rtt_ms)
        link = f"plus a simulated {rtt_ms:g} ms RTT ({HANDSHAKE_RTTS} RTTs on a new connection)"
    return RouterSpec(
        name,
        f"remote router at {host_of(url)}, client: {remote_client()}, link {link}",
        build,
        optional_env=(REMOTE_KEY_ENV,),
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
    """Close what a built router holds open, when it has a ``close()``."""
    closer = getattr(router, "close", None)
    if callable(closer):
        closer()


def plan_cost(spec: RouterSpec, requests: Sequence[RouteRequest]) -> float | None:
    """The estimated cost of calling ``spec`` once per request; ``0`` for a free router, ``None`` when unknown."""
    if spec.cost_usd is None:
        return 0.0
    costs = [spec.cost_usd(req) for req in requests]
    return None if any(c is None for c in costs) else sum(c for c in costs if c is not None)
