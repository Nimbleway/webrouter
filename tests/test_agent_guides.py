"""The agent skill and the baseline agent prompt keep up with the MCP server's tools.

Both teach an agent the MCP tools by name; a tool added to the server and left out of either is one the agent
never learns to use well. A tool may stay out of a guide only by being listed in :data:`UNNAMED`, with a reason.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from web_router.constants_mcp_hints import MCP_TOOL_HINTS

ROOT = Path(__file__).resolve().parent.parent
GUIDES = (ROOT / "skills" / "web-router" / "SKILL.md", ROOT / "docs" / "agent-prompt.md")

UNNAMED: dict[str, str] = {}
"""Tools a guide may leave out, each with why. Empty: every tool is worth a line."""


@pytest.mark.parametrize("guide", GUIDES, ids=lambda path: path.name)
def test_every_mcp_tool_is_named_in_the_guide(guide: Path) -> None:
    words = set(re.findall(r"[a-z_]+", guide.read_text(encoding="utf-8")))
    missing = sorted(set(MCP_TOOL_HINTS) - words - set(UNNAMED))
    assert not missing, f"{guide.relative_to(ROOT)} never names {missing}; add them or list them in UNNAMED"


def test_the_skip_list_names_only_real_tools() -> None:
    assert set(UNNAMED) <= set(MCP_TOOL_HINTS)
