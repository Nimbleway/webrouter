"""``frozen.py``'s command line: its arguments, parsed. ``frozen.main`` runs the command they name.

Imported as a sibling, by name, as ``_common`` is.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _frozen_files import FROZEN_DIR, JOINED_DIR


def parse(doc: str | None, argv: list[str] | None = None) -> argparse.Namespace:
    """The command and its options, from ``argv`` or the process's own arguments."""
    parser = argparse.ArgumentParser(description=doc.split("\n\n")[0] if doc else None)
    parser.add_argument("--frozen", type=Path, default=FROZEN_DIR, help=f"the frozen folder (default: {FROZEN_DIR})")
    parser.add_argument(
        "--dataset",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="a local copy of a question set (simpleqa, sealqa-hard) in place of the download",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    joining = commands.add_parser("join", help="verify, then put the questions back from the public datasets")
    joining.add_argument("--out", type=Path, default=JOINED_DIR, help=f"where to write (default: {JOINED_DIR})")
    commands.add_parser(
        "verify", help="check every frozen file against SHA256SUMS, and the failed-row counts against the rows"
    )
    building = commands.add_parser("build", help="write the frozen folder anew from scan folders")
    building.add_argument("results", type=Path, help="the folder holding the scans named in FROZEN_SETS")
    building.add_argument("--no-join-check", action="store_true", help="skip resolving ids against the datasets")
    return parser.parse_args(sys.argv[1:] if argv is None else argv)
