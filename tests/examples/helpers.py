"""What the example-router tests share: loading a script from ``examples/`` and a request to route."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from web_router import Candidate, RouteRequest

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def load_example(name: str) -> ModuleType:
    """Import ``examples/<name>.py`` as a module, as ``uv run python examples/<name>.py`` would find it."""
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CANDIDATES = (
    Candidate(tool="search", provider="tavily", est_cost_usd=0.008, est_latency_s=1.2),
    Candidate(tool="search", provider="exa"),
    Candidate(tool="answer", provider="perplexity", est_cost_usd=0.005),
    Candidate(tool="research", provider="parallel", est_cost_usd=0.1, est_latency_s=60.0),
)


def request(
    prompt: str = "who won the 2022 world cup",
    *,
    urls: tuple[str, ...] = (),
    candidates: tuple[Candidate, ...] = CANDIDATES,
) -> RouteRequest:
    """A request to route, over CANDIDATES unless told otherwise."""
    return RouteRequest(prompt=prompt, candidates=candidates, deadline_s=1.0, urls=urls)
