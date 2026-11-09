"""``python -m web_router.presets_sql upgrade <url>``: bring a presets database up to this version's schema."""

from __future__ import annotations

import argparse
import sys

from web_router.constants_presets import (
    MSG_PRESETS_UPGRADED,
    PRESETS_CLI_DESCRIPTION,
    PRESETS_CLI_PROG,
    PRESETS_CLI_UPGRADE,
    PRESETS_CLI_UPGRADE_HELP,
    PRESETS_CLI_URL_HELP,
)
from web_router.presets_sql import upgrade


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=PRESETS_CLI_PROG, description=PRESETS_CLI_DESCRIPTION)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(PRESETS_CLI_UPGRADE, help=PRESETS_CLI_UPGRADE_HELP).add_argument(
        "url", help=PRESETS_CLI_URL_HELP
    )
    arguments = parser.parse_args(argv)
    # The URL is never echoed: it can carry the database's password.
    print(MSG_PRESETS_UPGRADED.format(revision=upgrade(arguments.url)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
