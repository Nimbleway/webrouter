"""A configured key masked in every form a tool can hand it back in, and the remembered-path record never followed."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from tests.mcp.conftest import ok_on
from tests.mcp.env_helpers import KEY, write
from web_router.client import AsyncWebRouter
from web_router.constants import ENV_BRAVE_API_KEY, MCP_TOOL_FETCH_RAW, MCP_TOOL_SEARCH
from web_router.constants_vendors import VENDOR_MASK
from web_router.exceptions import ProviderError
from web_router.mcp.env import EnvSources
from web_router.mcp.env_pointer import pointer_file
from web_router.mcp.messages import error_text
from web_router.mcp.server import call_tool
from web_router.mcp.tools import WebRouterTools


def _basic(user: str) -> str:
    return base64.b64encode(f"{user}:{KEY}".encode()).decode()


def _escaped(every: bool) -> str:
    return "".join(f"\\u{ord(char):04x}" if every or char == "-" else char for char in KEY)


# A user name of each length mod 3 puts the key at each of base64's three alignments.
ENCODED = [
    pytest.param(f"Authorization: Basic {_basic(user)}", id=f"basic-{len(user)}") for user in ("", "u", "us", "usr")
] + [
    pytest.param(base64.b64encode(KEY.encode()).decode(), id="base64"),
    pytest.param(base64.urlsafe_b64encode(f"x:{KEY}".encode()).decode(), id="urlsafe"),
    pytest.param(f'{{"key": "{_escaped(True)}"}}', id="json-escaped"),
    pytest.param(f'{{"key": "{_escaped(False)}"}}', id="json-partly-escaped"),
]


def _unmasked(text: str) -> bool:
    """Whether ``text`` still holds the key in any form a test spelled it in."""
    return any(form in text for form in [KEY, _escaped(True), _escaped(False)]) or any(
        param.values[0] in text for param in ENCODED
    )


@pytest.mark.parametrize("echo", ENCODED)
def test_an_error_masks_a_key_base64_encoded_or_json_escaped(
    no_keys: None, monkeypatch: pytest.MonkeyPatch, echo: str
) -> None:
    monkeypatch.setenv(ENV_BRAVE_API_KEY, KEY)

    text = error_text(ProviderError("brave", f"rejected {echo}"))

    assert not _unmasked(text)
    assert VENDOR_MASK in text


def test_an_unpadded_base64_key_is_masked_to_its_last_character(no_keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BRAVE_API_KEY, KEY)
    unpadded = base64.urlsafe_b64encode(KEY.encode()).decode().rstrip("=")

    assert f"token {VENDOR_MASK} expired" in error_text(ProviderError("brave", f"token {unpadded} expired"))


@pytest.mark.parametrize("user", ["", "u", "us"])
def test_an_error_masks_a_short_key_inside_a_basic_header(
    no_keys: None, monkeypatch: pytest.MonkeyPatch, user: str
) -> None:
    monkeypatch.setenv(ENV_BRAVE_API_KEY, "abcd")
    header = base64.b64encode(f"{user}:abcd".encode()).decode()

    assert header not in error_text(ProviderError("brave", f"Authorization: Basic {header}"))


@pytest.mark.parametrize("echo", ENCODED)
async def test_fetch_raw_masks_a_key_base64_encoded_or_json_escaped(
    no_keys: None, monkeypatch: pytest.MonkeyPatch, echo: str
) -> None:
    monkeypatch.setenv(ENV_BRAVE_API_KEY, KEY)
    tools = WebRouterTools(AsyncWebRouter("auto"))
    record = tools.store.add(MCP_TOOL_SEARCH, raw={"echo": echo})

    echoed = (await ok_on(tools, MCP_TOOL_FETCH_RAW, handle=record.handle))["raw"]["echo"]

    assert not _unmasked(echoed)
    assert VENDOR_MASK in echoed


async def test_fetch_raw_masks_a_key_that_is_a_dict_key(no_keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BRAVE_API_KEY, KEY)
    tools = WebRouterTools(AsyncWebRouter("auto"))
    record = tools.store.add(MCP_TOOL_SEARCH, raw={"tokens": {KEY: "active", f"Bearer {KEY}": 1}})

    raw = (await ok_on(tools, MCP_TOOL_FETCH_RAW, handle=record.handle))["raw"]

    assert raw == {"tokens": {VENDOR_MASK: "active", f"Bearer {VENDOR_MASK}": 1}}


async def test_any_exception_a_tool_raises_is_masked(
    no_keys: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The model reads the masked text; the operator's log gets the traceback, masked too."""
    monkeypatch.setenv(ENV_BRAVE_API_KEY, KEY)
    tools = WebRouterTools(AsyncWebRouter("auto"))

    async def raises(name: str, arguments: Any) -> dict[str, Any]:
        raise RuntimeError(f"connection reset while sending api_key={KEY}")

    monkeypatch.setattr(tools, "call", raises)
    result = await call_tool(tools, MCP_TOOL_SEARCH, {"query": "q"})

    assert result.is_error
    assert KEY not in result.content[0].text
    assert VENDOR_MASK in result.content[0].text
    logged = caplog.text
    assert "in raises" in logged and "RuntimeError" in logged
    assert KEY not in logged


async def test_a_failure_carried_inside_a_result_is_masked(no_keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """An extract answers with each URL that failed and why; that why is an exception's text too."""
    monkeypatch.setenv(ENV_BRAVE_API_KEY, KEY)
    tools = WebRouterTools(AsyncWebRouter("auto"))

    async def answers(name: str, arguments: Any) -> dict[str, Any]:
        return {"failures": [{"url": "https://example.com", "error": f"HTTP 401: key {KEY} revoked"}]}

    monkeypatch.setattr(tools, "call", answers)
    result = await call_tool(tools, MCP_TOOL_SEARCH, {"query": "q"})

    assert not result.is_error
    assert KEY not in result.content[0].text
    assert result.structured_content == {
        "failures": [{"url": "https://example.com", "error": "HTTP 401: key *** revoked"}]
    }


def test_remember_refuses_a_symlink_planted_at_the_record(isolated_home: Path) -> None:
    elsewhere = write(isolated_home / "elsewhere.txt", "untouched\n")
    pointer_file().parent.mkdir(parents=True, exist_ok=True)
    pointer_file().symlink_to(elsewhere)
    path = write(isolated_home / "project" / ".env", f"{ENV_BRAVE_API_KEY}={KEY}\n")

    with pytest.raises(ValueError, match="could not be recorded"):
        EnvSources().load(str(path), remember=True)

    assert elsewhere.read_text(encoding="utf-8") == "untouched\n"
    assert pointer_file().is_symlink()


URLSAFE_KEY = "key-for-urlsafe>>>???"
"""A key whose base64 holds ``+`` and ``/`` at every alignment, so its URL-safe form differs from the standard one."""


@pytest.mark.parametrize("user", ["", "u", "us"])
async def test_a_key_is_masked_in_its_url_safe_base64_form(
    no_keys: None, monkeypatch: pytest.MonkeyPatch, user: str
) -> None:
    monkeypatch.setenv(ENV_BRAVE_API_KEY, URLSAFE_KEY)
    echo = base64.urlsafe_b64encode(f"{user}:{URLSAFE_KEY}".encode()).decode()
    assert echo != base64.b64encode(f"{user}:{URLSAFE_KEY}".encode()).decode()
    tools = WebRouterTools(AsyncWebRouter("auto"))
    record = tools.store.add(MCP_TOOL_SEARCH, raw={"echo": echo})

    error = error_text(ProviderError("brave", f"token {echo}"))
    echoed = (await ok_on(tools, MCP_TOOL_FETCH_RAW, handle=record.handle))["raw"]["echo"]

    for masked in (error, echoed):
        assert echo not in masked
        assert VENDOR_MASK in masked
