"""Changelog fragments: one file per pull request in ``changelog.d/``, collected at release.

A pull request with a change users would notice adds ``changelog.d/<name>.<type>.md``,
where ``<name>`` is any short slug (the branch name works) and ``<type>`` is one of
``TYPES``. The file holds one entry, the text of a single bullet without its ``- ``.
Since every pull request adds its own file, no two of them edit the same lines, so
fragments never conflict the way lines at the top of ``CHANGELOG.md`` did.

At release, ``make changelog`` moves every fragment under ``## [Unreleased]`` in
``CHANGELOG.md``, newest first within its section, and deletes the files.

    make changelog                                # collect the fragments
    uv run python -m scripts.changelog --check    # exit 1 if a fragment is malformed
    uv run python -m scripts.changelog --preview  # print the result, write nothing
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRAGMENT_DIR = ROOT / "changelog.d"
CHANGELOG = ROOT / "CHANGELOG.md"
UNRELEASED = "## [Unreleased]"
# Keep a Changelog's section order, which is also the order new sections are created in.
TYPES = {
    "added": "Added",
    "changed": "Changed",
    "deprecated": "Deprecated",
    "removed": "Removed",
    "fixed": "Fixed",
    "security": "Security",
}
MAX_CHARS = 400
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*\.(?P<type>[a-z]+)\.md$")
NOT_FRAGMENTS = {"README.md"}

BAD_NAME = "{file}: name it <name>.<type>.md, with a lowercase slug and a type from: {types}."
BAD_TYPE = "{file}: '{type}' is not a changelog type; use one of: {types}."
EMPTY = "{file} is empty; write the entry, or delete the file and label the PR skip-changelog."
TOO_LONG = "{file} is {chars} characters; an entry is at most {limit}. Say what changed for users, not how."
BULLET = (
    "{file} has a line that starts a bullet or a heading; write one entry as plain text, "
    "without the leading '- ', which is added when it is collected."
)
PARAGRAPHS = "{file} holds more than one paragraph; an entry is one bullet. Split it into two fragments."
GIT_FAILED = "warning: git could not date {file} ({reason}); it is ordered by name."
NO_UNRELEASED = "CHANGELOG.md has no '## [Unreleased]' heading to collect fragments under."


@dataclass(frozen=True)
class Fragment:
    path: Path
    type: str
    text: str


def fragment_paths(directory: Path | None = None) -> list[Path]:
    """Every file in ``directory`` (``changelog.d/``) that should be a fragment, in name order."""
    directory = directory or FRAGMENT_DIR
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and p.name not in NOT_FRAGMENTS)


def problems(directory: Path | None = None) -> list[str]:
    """What is wrong with the fragments in ``directory``; empty when every one is valid."""
    found: list[str] = []
    types = ", ".join(TYPES)
    for path in fragment_paths(directory):
        file = f"changelog.d/{path.name}"
        match = NAME.match(path.name)
        if match is None:
            found.append(BAD_NAME.format(file=file, types=types))
            continue
        if match["type"] not in TYPES:
            found.append(BAD_TYPE.format(file=file, type=match["type"], types=types))
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            found.append(EMPTY.format(file=file))
        elif len(text) > MAX_CHARS:
            found.append(TOO_LONG.format(file=file, chars=len(text), limit=MAX_CHARS))
        elif re.search(r"^\s*(?:[-*+] |#+\s)", text, re.MULTILINE):
            found.append(BULLET.format(file=file))
        elif re.search(r"\n\s*\n", text):
            found.append(PARAGRAPHS.format(file=file))
    return found


def added_at(path: Path) -> int:
    """When git most recently added ``path``, as a Unix time; 0 for a fragment not committed yet.

    When git can't answer, the fragment sorts by name, and a warning says so.
    """
    try:
        run = subprocess.run(
            ["git", "log", "--diff-filter=A", "--format=%ct", "--", path.name],
            cwd=path.parent,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        print(GIT_FAILED.format(file=path.name, reason=exc), file=sys.stderr)
        return 0
    if run.returncode:
        print(GIT_FAILED.format(file=path.name, reason=run.stderr.strip()), file=sys.stderr)
        return 0
    out = run.stdout.split()
    return int(out[0]) if out else 0


def load(directory: Path | None = None) -> list[Fragment]:
    """The valid fragments, newest first; call ``problems`` first to refuse a bad set."""
    fragments: list[Fragment] = []
    for path in fragment_paths(directory):
        match = NAME.match(path.name)
        if match is not None:
            fragments.append(Fragment(path=path, type=match["type"], text=path.read_text(encoding="utf-8").strip()))
    # Newest first, matching the changelog; a name breaks ties so the order is stable.
    fragments.sort(key=lambda f: (-added_at(f.path), f.path.name))
    return fragments


def _unreleased_span(lines: list[str]) -> tuple[int, int]:
    """The line indexes from ``## [Unreleased]`` up to (not including) the next ``## `` heading."""
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == UNRELEASED)
    except StopIteration:
        raise ValueError(NO_UNRELEASED) from None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start, end


def _insert_section(lines: list[str], heading: str, entries: list[str]) -> None:
    """Put ``entries`` at the top of ``heading`` in the Unreleased block, creating it in Keep a Changelog order."""
    start, end = _unreleased_span(lines)
    title = f"### {heading}"
    existing = next((i for i in range(start, end) if lines[i].strip() == title), None)
    if existing is not None:
        first = existing + 1
        while first < end and not lines[first].strip():
            first += 1
        lines[first:first] = entries
        return
    order = list(TYPES.values())
    later = order[order.index(heading) + 1 :]
    before = next((i for i in range(start, end) if lines[i].startswith("### ") and lines[i][4:].strip() in later), end)
    while before > start + 1 and not lines[before - 1].strip():
        before -= 1
    lines[before:before] = ["", title, "", *entries]


def collect(changelog: str, fragments: list[Fragment]) -> str:
    """``changelog`` with every fragment added under ``## [Unreleased]``."""
    lines = changelog.splitlines()
    for type_, heading in TYPES.items():
        entries = ["- " + " ".join(f.text.splitlines()) for f in fragments if f.type == type_]
        if entries:
            _insert_section(lines, heading, entries)
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Collect changelog.d/ fragments into CHANGELOG.md.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="only check the fragments; exit 1 if one is malformed")
    mode.add_argument("--preview", action="store_true", help="print the collected CHANGELOG.md; write nothing")
    args = parser.parse_args(argv)
    found = problems()
    if found:
        print("\n".join(found), file=sys.stderr)
        return 1
    if args.check:
        return 0
    fragments = load()
    try:
        text = collect(CHANGELOG.read_text(encoding="utf-8"), fragments)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.preview:
        print(text, end="")
        return 0
    CHANGELOG.write_text(text, encoding="utf-8")
    for fragment in fragments:
        fragment.path.unlink()
    print(f"Collected {len(fragments)} fragment(s) into CHANGELOG.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
