"""What the operator hears on stderr at startup, and every configured key masked by value before a model sees it."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import pytest

from tests.mcp.conftest import ok_on
from tests.mcp.env_helpers import KEY, write
from web_router.client import AsyncWebRouter
from web_router.constants import (
    ENV_AGENTCORE_GATEWAY_URL,
    ENV_BRAVE_API_KEY,
    ENV_JINA_API_KEY,
    ENV_MCP_ENV_FILE,
    MCP_ENV_FILE,
    MCP_TOOL_FETCH_RAW,
    MCP_TOOL_SEARCH,
)
from web_router.constants_mcp import ENV_MCP_MAX_COST_USD
from web_router.constants_vendors import VENDOR_MASK
from web_router.exceptions import ProviderError
from web_router.mcp.env import EnvSources
from web_router.mcp.env_pointer import pointer_file
from web_router.mcp.messages import error_text
from web_router.mcp.tools import WebRouterTools

_LOGGER = "web_router.mcp.env_warnings"


def _warnings(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(record.getMessage() for record in caplog.records if record.name == _LOGGER)


@pytest.fixture
def warned(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    monkeypatch.setenv(ENV_MCP_MAX_COST_USD, "5")  # so only the warning a test is about is said
    return caplog


def test_keys_from_a_remembered_file_are_said_on_stderr(isolated_home: Path, warned: Any) -> None:
    remembered = write(isolated_home / "other" / ".env", f"{ENV_BRAVE_API_KEY}={KEY}\n")
    EnvSources().load(str(remembered), remember=True)
    os.environ.pop(ENV_BRAVE_API_KEY)

    EnvSources.at_startup()

    said = _warnings(warned)
    assert str(remembered) in said and str(pointer_file()) in said and "forget" in said
    assert KEY not in said


def test_a_missing_explicit_file_is_said_with_where_the_search_went(
    isolated_home: Path, warned: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_MCP_ENV_FILE, str(isolated_home / "wrong" / ".env"))

    EnvSources.at_startup()

    assert f"{ENV_MCP_ENV_FILE} names no readable file" in _warnings(warned)


@pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX")
@pytest.mark.parametrize(("mode", "said"), [(0o644, True), (0o600, False)])
@pytest.mark.parametrize("exported", [False, True], ids=["new", "nothing new"])
def test_a_key_file_others_can_read_is_said(
    isolated_home: Path, warned: Any, monkeypatch: pytest.MonkeyPatch, mode: int, said: bool, exported: bool
) -> None:
    if exported:
        monkeypatch.setenv(ENV_BRAVE_API_KEY, "exported")
    write(Path.cwd() / MCP_ENV_FILE, f"{ENV_BRAVE_API_KEY}={KEY}\n").chmod(mode)

    EnvSources.at_startup()

    assert ("chmod 600" in _warnings(warned)) is said


@pytest.mark.parametrize(("ceiling", "said"), [("", True), ("5", False)])
def test_billed_keys_with_no_ceiling_are_said(
    isolated_home: Path, warned: Any, monkeypatch: pytest.MonkeyPatch, ceiling: str, said: bool
) -> None:
    monkeypatch.setenv(ENV_MCP_MAX_COST_USD, ceiling)
    write(Path.cwd() / MCP_ENV_FILE, f"{ENV_BRAVE_API_KEY}={KEY}\n").chmod(0o600)

    EnvSources.at_startup()

    assert (ENV_MCP_MAX_COST_USD in _warnings(warned)) is said


@pytest.mark.parametrize("echo", ["invalid key {key}", "Authorization: Bearer {key}", "url?k={key}&x=1"])
@pytest.mark.parametrize("variable", [ENV_BRAVE_API_KEY, ENV_JINA_API_KEY], ids=["billed", "optional"])
def test_an_error_that_echoes_a_configured_key_in_plain_text_masks_it(
    no_keys: None, monkeypatch: pytest.MonkeyPatch, echo: str, variable: str
) -> None:
    monkeypatch.setenv(variable, KEY)

    text = error_text(ProviderError("brave", echo.format(key=KEY)))

    assert KEY not in text
    assert VENDOR_MASK in text


def test_an_error_masks_a_short_key_even_inside_a_longer_word(no_keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BRAVE_API_KEY, "abc12")

    assert "abc12" not in error_text(ProviderError("brave", "token abc12suffix rejected"))


async def test_fetch_raw_masks_a_configured_key_but_leaves_a_free_setting(
    no_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_BRAVE_API_KEY, KEY)
    monkeypatch.setenv("DD_SITE", "us5.datadoghq.com")
    tools = WebRouterTools(AsyncWebRouter("auto"))
    record = tools.store.add(MCP_TOOL_SEARCH, raw={"echo": f"token {KEY}", "site": "us5.datadoghq.com"})

    raw = (await ok_on(tools, MCP_TOOL_FETCH_RAW, handle=record.handle))["raw"]

    assert raw == {"echo": f"token {VENDOR_MASK}", "site": "us5.datadoghq.com"}


def test_an_error_keeps_a_setting_that_is_no_secret(no_keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_AGENTCORE_GATEWAY_URL, "not-a-url-at-all")

    assert "not-a-url-at-all" in error_text(ProviderError("agentcore", "got 'not-a-url-at-all'"))


async def test_fetch_raw_masks_a_short_key_as_a_whole_word_only(no_keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A payload is data: a short key is masked where it stands alone, never inside a longer word."""
    monkeypatch.setenv(ENV_BRAVE_API_KEY, "abc12")
    tools = WebRouterTools(AsyncWebRouter("auto"))
    record = tools.store.add(MCP_TOOL_SEARCH, raw={"echo": "key abc12", "word": "abc12suffix"})

    raw = (await ok_on(tools, MCP_TOOL_FETCH_RAW, handle=record.handle))["raw"]

    assert raw == {"echo": f"key {VENDOR_MASK}", "word": "abc12suffix"}


def test_a_short_key_never_hides_a_session_url(no_keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BRAVE_API_KEY, "https")

    text = error_text(ProviderError("brave", "session at https://cdp.example.com/?token=sessionsecret9 ended"))

    assert "sessionsecret9" not in text
