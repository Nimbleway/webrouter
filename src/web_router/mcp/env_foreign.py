"""A remembered ``.env`` from another checkout: which files count, and how the server says so."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from web_router.constants import MCP_ENV_SOURCE_REMEMBERED, MCP_ENV_STATUS_USED
from web_router.constants_mcp_env import MCP_MSG_ENV_FOREIGN, MCP_MSG_INSTRUCTIONS_FOREIGN_ENV

if TYPE_CHECKING:
    from web_router.mcp.env import EnvLookup


def foreign_files(searched: list[EnvLookup]) -> list[Path]:
    """The remembered files loaded at startup that lie outside the working directory: keys from another project."""
    # Resolved, so a link inside the project to a file elsewhere still counts as elsewhere.
    here = Path.cwd().resolve()
    # Started in the home folder, `/` or a parent of it, no project is meant: every remembered file is elsewhere.
    no_project = Path.home().resolve().is_relative_to(here)
    return [
        lookup.path
        for lookup in searched
        if lookup.source == MCP_ENV_SOURCE_REMEMBERED
        and lookup.status == MCP_ENV_STATUS_USED
        and (no_project or not lookup.path.resolve().is_relative_to(here))
    ]


def foreign_warning(paths: list[Path], pointer: Path) -> dict[str, str]:
    """The ``warning`` entry of the capabilities report: empty when no file is foreign."""
    if not paths:
        return {}
    return {"warning": MCP_MSG_ENV_FOREIGN.format(paths=_listed(paths), pointer=pointer)}


def noted_instructions(instructions: str, paths: list[Path]) -> str:
    """``instructions`` with a warning added when ``paths`` holds a foreign file."""
    return instructions + (MCP_MSG_INSTRUCTIONS_FOREIGN_ENV.format(paths=_listed(paths)) if paths else "")


def _listed(paths: list[Path]) -> str:
    return ", ".join(map(str, paths))
