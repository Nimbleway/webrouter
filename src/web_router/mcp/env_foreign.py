"""A remembered ``.env`` from another checkout: which files count, and how the server says so."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from web_router.constants import MCP_ENV_SOURCE_REMEMBERED, MCP_ENV_STATUS_USED
from web_router.constants_mcp_env import MCP_MSG_ENV_FOREIGN, MCP_MSG_INSTRUCTIONS_FOREIGN_ENV
from web_router.mcp.env_credentials import free_variables

if TYPE_CHECKING:
    from web_router.mcp.env import EnvLookup
    from web_router.mcp.env_credentials import EnvLoad


def foreign_files(searched: list[EnvLookup], startup: list[EnvLoad]) -> list[Path]:
    """The remembered files from outside the working directory that loaded a billed key: keys from another project.

    A file that set only free names -- Jina's optional key, which raises a
    keyless backend's rate limit, or a vendor setting such as ``DD_SITE`` --
    bills nothing, as the read-only tool hints say.
    """
    return [path for path in remembered_billed(searched, startup) if not in_project(path)]


def remembered_billed(searched: list[EnvLookup], startup: list[EnvLoad]) -> list[Path]:
    """The remembered files, from anywhere, that loaded a billed key at startup."""
    free = free_variables()
    billed = {load.path for load in startup if set(load.loaded) - free}
    return [
        lookup.path
        for lookup in searched
        if lookup.source == MCP_ENV_SOURCE_REMEMBERED and lookup.status == MCP_ENV_STATUS_USED and lookup.path in billed
    ]


def in_project(path: Path) -> bool:
    """Whether ``path`` lies inside the directory the server started in, taken as the project.

    Resolved, so a link inside the project to a file elsewhere still counts as
    elsewhere. Started in the home folder, ``/`` or a parent of it, no project
    is meant, so nothing is inside one.
    """
    here = Path.cwd().resolve()
    return not Path.home().resolve().is_relative_to(here) and path.resolve().is_relative_to(here)


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
