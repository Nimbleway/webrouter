"""Load a script from ``benchmarks/`` as it runs from its folder, its neighbours imported as siblings by plain name.

The benchmark scripts are not a package: each imports the modules beside it
by plain name (``from _common import ...``), as it does when run from its
folder. ``scripts/latency.py`` and the benchmark tests load them through
:func:`load_script`, so there is one loader and one list of ``frozen.py``'s
siblings.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

BENCHMARKS = Path(__file__).resolve().parent.parent / "benchmarks" / "providers"
FROZEN_SCRIPT = BENCHMARKS / "frozen.py"
FROZEN_SIBLINGS = ("_common", "_frozen_checks", "_frozen_mixed", "_tools_effort_plan")
"""What ``frozen.py`` imports from its folder by plain name."""


@contextmanager
def load_script(path: Path, name: str, siblings: Iterable[str]) -> Iterator[ModuleType]:
    """The script at ``path`` loaded as module ``name``, importing its neighbours as siblings.

    ``siblings`` are what it imports from its folder under plain names. Once
    the block ends, they and ``name`` are put back as they were: one another
    loader imported first is kept, the rest are forgotten.
    """
    before = {n: sys.modules[n] for n in (*siblings, name) if n in sys.modules}
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # Registered first: a dataclass looks its module up by name.
        sys.modules[name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(path.parent))
        for n in (*siblings, name):
            if n in before:
                sys.modules[n] = before[n]
            else:
                sys.modules.pop(n, None)
