"""``server.json`` lists the MCP server in the MCP Registry, which accepts it only when it matches the PyPI release."""

import json
import re
import tomllib
from pathlib import Path

from web_router import __version__

ROOT = Path(__file__).resolve().parents[1]
SERVER = json.loads((ROOT / "server.json").read_text())
PACKAGE = SERVER["packages"][0]


def test_versions_match_the_release() -> None:
    """A release bumps ``_version.py``; the registry entry must name that same version, or it points at an older one."""
    assert SERVER["version"] == __version__
    assert PACKAGE["version"] == __version__
    assert PACKAGE["runtimeArguments"][0]["value"] == f"webrouter[mcp]=={__version__}"


def test_package_is_this_distribution() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert PACKAGE["identifier"] == project["name"]
    assert PACKAGE["runtimeArguments"][1]["value"] in project["scripts"]


def test_readme_proves_ownership() -> None:
    """The registry checks the PyPI description, built from the README, for an ``mcp-name:`` line with this name."""
    names = re.findall(r"mcp-name: (\S+?)(?:\s|-->|$)", (ROOT / "README.md").read_text())
    assert names == [SERVER["name"]]


def test_description_fits_the_registry() -> None:
    assert 1 <= len(SERVER["description"]) <= 100
