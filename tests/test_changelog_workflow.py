"""Execute the workflow's file-policy check against GitHub-shaped file metadata."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/changelog.yml"
FRAGMENT = {"status": "added", "filename": "changelog.d/fix.fixed.md"}


@pytest.mark.parametrize(
    ("rows", "count", "error"),
    [
        ([FRAGMENT], 1, None),
        ([FRAGMENT, {"status": "modified", "filename": "docs/archive notes.md"}], 2, None),
        (
            [FRAGMENT, {"status": "renamed", "filename": "docs/archive notes.md", "previous_filename": "CHANGELOG.md"}],
            2,
            "Undo your edit",
        ),
        ([FRAGMENT, {"status": "modified", "filename": "CHANGELOG.md"}], 2, "Undo your edit"),
        ([FRAGMENT], 3001, "Incomplete PR file list"),
        ([], 0, "No changelog fragment"),
        ([{"status": "modified", "filename": "changelog.d/fix.fixed.md"}], 1, "No changelog fragment"),
    ],
)
def test_file_policy_preserves_names_and_requires_a_complete_list(
    tmp_path: Path, rows: list[dict[str, str]], count: int, error: str | None
) -> None:
    script = textwrap.dedent(WORKFLOW.read_text().split("python3 - <<'PY'\n", 1)[1].rsplit("          PY", 1)[0])
    (tmp_path / "files.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
    run = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "EXPECTED_FILES": str(count)},
        capture_output=True,
        text=True,
        check=False,
    )
    if error is None:
        assert run.returncode == 0, run.stderr
        assert "changelog.d/fix.fixed.md" in run.stdout
    else:
        assert run.returncode == 1
        assert error in run.stderr
