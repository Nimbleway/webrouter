"""Writing a generated data file, or with ``--check`` only saying whether the committed one is stale.

Shared by ``chains.py`` and ``efforts.py``, imported as a sibling by name like
``_common.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

MSG_STALE = "{path} is stale: run `{command}` and commit the result"
MSG_WRITTEN = "wrote {path}"


def write_or_check(wanted: str, path: Path, check: bool, command: str) -> int:
    """Write ``wanted`` to ``path``, or with ``check`` exit code 1 when ``path`` is missing or differs."""
    if check:
        if not path.is_file() or path.read_text(encoding="utf-8") != wanted:
            print(MSG_STALE.format(path=path, command=command), file=sys.stderr)
            return 1
        return 0
    path.write_text(wanted, encoding="utf-8")
    print(MSG_WRITTEN.format(path=path))
    return 0
