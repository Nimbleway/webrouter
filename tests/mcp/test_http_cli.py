"""The command line's side of the HTTP transport: the bind it passes on, and the ones it refuses."""

from __future__ import annotations

from pathlib import Path

import pytest

from web_router import mcp_cli
from web_router.constants_mcp_env import (
    ENV_MCP_LOAD_ENV,
    ENV_MCP_PRIVATE_URLS,
    MCP_LOAD_ENV_OFF,
    MCP_PRIVATE_URLS_OFF,
)
from web_router.constants_mcp_http import ENV_MCP_TOKEN, MCP_HTTP_DEFAULT_HOST, MCP_HTTP_DEFAULT_PORT
from web_router.mcp import http_transport
from web_router.mcp.env_policy import switch


def _never_serves(*args: object) -> None:
    raise AssertionError("an open bind without a token started serving")


def test_the_command_refuses_an_open_bind_before_serving(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(ENV_MCP_TOKEN, raising=False)
    monkeypatch.setattr(http_transport.anyio, "run", _never_serves)

    with pytest.raises(SystemExit) as exited:
        mcp_cli.main(["--transport", "http", "--host", "0.0.0.0"])

    assert exited.value.code == 2
    assert ENV_MCP_TOKEN in capsys.readouterr().err


def test_host_without_http_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exited:
        mcp_cli.main(["--host", "0.0.0.0"])

    assert exited.value.code == 2


@pytest.mark.parametrize("port", ["0", "-1", "65536", "http"])
def test_a_port_out_of_range_is_a_usage_error(port: str) -> None:
    with pytest.raises(SystemExit) as exited:
        mcp_cli.main(["--transport", "http", "--port", port])

    assert exited.value.code == 2


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], (MCP_HTTP_DEFAULT_HOST, MCP_HTTP_DEFAULT_PORT)),
        (["--port", "9000"], (MCP_HTTP_DEFAULT_HOST, 9000)),
        (["--host", "::1", "--port", "1"], ("::1", 1)),
    ],
)
def test_the_command_passes_the_bind_it_was_given(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], expected: tuple[str, int]
) -> None:
    served: list[tuple[str, int]] = []
    monkeypatch.setattr(http_transport, "main", lambda host, port: served.append((host, port)))

    mcp_cli.main(["--transport", "http", *argv])

    assert served == [expected]


def test_the_http_command_turns_load_env_and_private_addresses_off(
    monkeypatch: pytest.MonkeyPatch, stdio_switches: None, isolated_home: Path
) -> None:
    served: list[object] = []
    monkeypatch.setattr(http_transport.anyio, "run", lambda *args: served.append(args))

    http_transport.main(MCP_HTTP_DEFAULT_HOST, MCP_HTTP_DEFAULT_PORT)

    assert served
    assert switch(ENV_MCP_LOAD_ENV) == MCP_LOAD_ENV_OFF
    assert switch(ENV_MCP_PRIVATE_URLS) == MCP_PRIVATE_URLS_OFF
