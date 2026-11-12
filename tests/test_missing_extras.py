"""A missing optional dependency names the extra that installs it, and ``all`` carries the MCP server."""

from __future__ import annotations

import builtins
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from web_router import mcp_cli

ROOT = Path(__file__).resolve().parents[1]


def _failing_import(module: str, missing: str) -> Callable[..., Any]:
    """An ``__import__`` that fails for ``module`` as if ``missing`` were not installed."""
    real = builtins.__import__

    def fake(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == module:
            raise ModuleNotFoundError(f"No module named {missing!r}", name=missing)
        return real(name, *args, **kwargs)

    return fake


def test_the_mcp_command_without_its_extra_prints_the_install_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(builtins, "__import__", _failing_import("web_router.mcp.server", "mcp"))

    with pytest.raises(SystemExit) as exited:
        mcp_cli.main()

    assert exited.value.code == 1
    assert 'pip install "webrouter[mcp]"' in capsys.readouterr().err


def test_an_unrelated_missing_module_is_not_reported_as_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bug inside the server must surface as itself, not as an install hint."""
    monkeypatch.setattr(builtins, "__import__", _failing_import("web_router.mcp.server", "no_such_module"))

    with pytest.raises(ModuleNotFoundError, match="no_such_module"):
        mcp_cli.main()


def test_the_http_base_classes_without_httpx_name_the_search_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    import web_router.providers as providers

    monkeypatch.delitem(sys.modules, "web_router.providers._http", raising=False)
    monkeypatch.setattr(builtins, "__import__", _failing_import("web_router.providers._http", "httpx"))

    with pytest.raises(ModuleNotFoundError, match=r'pip install "webrouter\[search\]"'):
        providers.HttpSearchProvider  # noqa: B018 -- the lookup is what is tested


def test_all_installs_the_mcp_server_and_the_console_script_can_name_its_extra() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    (all_extra,) = project["optional-dependencies"]["all"]

    assert "mcp" in all_extra.split("[")[1].rstrip("]").split(",")
    assert project["scripts"]["web-router-mcp"] == "web_router.mcp_cli:main"
