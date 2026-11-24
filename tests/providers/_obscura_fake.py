"""A stand-in ``obscura`` binary for the provider's unit tests.

The fake records its argv and pid beside itself, then behaves as ``FAKE_OBSCURA``
in its environment says, so a test exercises the real subprocess path offline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

FAKE = """\
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls.jsonl"), "a") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "pid": os.getpid()}) + "\\n")
mode = os.environ.get("FAKE_OBSCURA", "page")
if mode == "page":
    sys.stdout.write("# Fetched\\n\\n" + sys.argv[-1])
elif mode == "fail":
    sys.stderr.write("Error: Failed to navigate: dns error\\n")
    sys.exit(1)
elif mode == "binary":
    sys.stdout.buffer.write(b"%PDF-1.5\\n\\xff\\xfe\\x00binary")
elif mode == "pdf":
    sys.stdout.write("%PDF-1.5\\n1 0 obj << /Type /Catalog >> endobj\\n" * 20)
elif mode == "blank":
    sys.stdout.write("  \\n")
elif mode == "hang":
    time.sleep(60)
"""


def make_fake(directory: Path) -> Path:
    """Write the fake binary into ``directory`` and return its path."""
    path = directory / "obscura"
    path.write_text(f"#!{sys.executable}\n{FAKE}")
    path.chmod(0o755)
    return path


def calls(fake: Path) -> list[dict[str, object]]:
    """Each run of ``fake``: its arguments and pid, oldest first."""
    log = fake.parent / "calls.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
