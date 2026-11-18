"""The command line's side of the HTTP transport: the bind it passes on, and the ones it refuses."""

from __future__ import annotations

import pytest

from web_router import mcp_cli
from web_router.constants_mcp_http import ENV_MCP_TOKEN, MCP_HTTP_DEFAULT_HOST, MCP_HTTP_DEFAULT_PORT
from web_router.mcp import http_transport


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
