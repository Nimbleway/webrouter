"""router-latency's routers: each one named on the command line, how it is built, what it needs and what it costs.

``rules`` is the library's built-in router, the one the MCP server attaches.
``llm`` is the built-in ``router="llm"``, on the preset ``$LLM_ROUTER_PRESET``
names (``openai`` unless set). ``sklearn`` and ``jev`` are the example routers
in ``examples/``, loaded from their files as route-eval loads a
``path/file.py:attr`` router.
``remote:<url>`` is the library's own ``remote`` router, built in
``router_latency_remote``. Any other ``module:attr`` or ``path/file.py:attr``
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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

from web_router.constants_decider import LLM_PRESETS, LLM_REPLY_FORMAT
from web_router.routing.contract import RouteRequest
from web_router.routing.decider_llm import llm_router, menu, schema
from web_router.routing.decider_question import question_for

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

RULES, SKLEARN, LLM, JEV, REMOTE = "rules", "sklearn", "llm", "jev", "remote:"
KNOWN = (RULES, SKLEARN, LLM, JEV, REMOTE + "<url>")

LLM_KEY_ENV, LLM_MODEL_ENV, LLM_PRESET_ENV = "OPENAI_API_KEY", "LLM_ROUTER_MODEL", "LLM_ROUTER_PRESET"
JEV_KEY_ENV = "TYPESAFE_API_KEY"
CHARS_PER_TOKEN = 4
"""A rough English-text token size, for the dry run's estimate only."""
MTOK = 1_000_000

MSG_BAD_ROUTER = "{spec!r}: a router is one of {known}, or module:attr / path/file.py:attr"
MSG_NO_SKLEARN = "the sklearn router needs scikit-learn: uv run --all-extras --with scikit-learn python ..."


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


def llm_tokens(req: RouteRequest) -> tuple[int, int]:
    """Estimated input and output tokens of one ``llm`` router call: instructions, options and schema; a ranking."""
    question = question_for(req)
    sent = question.instructions + LLM_REPLY_FORMAT + menu(question) + json.dumps(schema(question))
    ranking = json.dumps({"ranked": list(question.options), "confidence": 0.5})
    return math.ceil(len(sent) / CHARS_PER_TOKEN), math.ceil(len(ranking) / CHARS_PER_TOKEN)


def _llm(model: str | None, price: tuple[float, float] | None) -> RouterSpec:
    preset = os.environ.get(LLM_PRESET_ENV) or "openai"
    key_env = LLM_PRESETS[preset][1] if preset in LLM_PRESETS else LLM_KEY_ENV

    def build() -> Any:
        return llm_router(preset=preset, model=model or os.environ[LLM_MODEL_ENV])

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
    env = (key_env,) if model else (key_env, LLM_MODEL_ENV)
    return RouterSpec(LLM, f'router="llm" (built in), model {shown}', build, env, (LLM_PRESET_ENV,), cost, note)


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
        from router_latency_remote import remote_spec  # here: that module imports RouterSpec from this one

        return [remote_spec(spec, rtt) for rtt in (0.0, *rtts_ms)]
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
        return router_specs(spec)[0]
    return _custom(spec)
