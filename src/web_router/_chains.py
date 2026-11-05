"""The default ``auto`` orders that route-eval ranked, read from ``data/chains.json``.

Search's keyed members and answer's members are ranked from the frozen benchmark
results by ``make chains`` (``benchmarks/providers/chains.py``),
which writes the file; never edit it by hand. A new backend joins by hand at the
end of its method's ``appended`` (search) or ``preferred`` (answer) list, where
the next ``make chains`` keeps it until frozen results rank it. Experimental: the
file's shape may change before 1.0.

Imports nothing from ``web_router``: :mod:`web_router.constants` and
:mod:`web_router.constants_answer` read it while they load.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

CHAINS_DATA_DIR = "data"
CHAINS_DATA_FILE = "chains.json"


@lru_cache(maxsize=1)
def chains() -> dict[str, Any]:
    """The whole generated file."""
    text = (resources.files("web_router") / CHAINS_DATA_DIR / CHAINS_DATA_FILE).read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    return data


def chain(method: str, part: str) -> tuple[str, ...]:
    """One list of a method's generated order: ``preferred``, or search's ``appended``."""
    return tuple(chains()[method][part])
