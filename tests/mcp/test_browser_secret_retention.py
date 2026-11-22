"""A session's secrets are masked while it is open, and gone once it ends.

``BrowserSessions`` masks the values of the sessions it holds. Holding them for
the life of the server instead would keep the CDP URL and the backend's session
id of every session ever opened -- and would make masking slower with every one
of them. What this pins: only the open sessions' values are held, and the ones
of a closed session stop being masked because they are no longer there.
"""

from __future__ import annotations

from types import SimpleNamespace

from pydantic import SecretStr

from web_router.constants import MCP_BROWSER_END_CLOSED, MCP_REDACTED
from web_router.mcp.browsers import BrowserSessions

SHARED_QUERY = "apiKey=shared-account-key"
"""Like a real key: one account's key rides in every CDP URL of that account."""


class _Handle:
    def __init__(self, index: int) -> None:
        self.cdp_url = f"wss://backend.example/devtools/browser/session-{index}?{SHARED_QUERY}"
        self.session_id = f"backend-session-{index}"
        self.live_view_url = f"https://live.example/view/{index}"
        self.session = SimpleNamespace(control_url=SecretStr(f"https://control.example/{index}"))

    def on_connect(self, install: object) -> None:
        return None

    async def release(self) -> None:
        return None


def _opener(index: int):
    async def open_session() -> _Handle:
        return _Handle(index)

    return open_session


async def test_a_closed_sessions_secrets_are_not_kept() -> None:
    sessions = BrowserSessions()
    survivor = await sessions.open(_opener(0))
    handle = sessions._sessions[survivor].handle
    for index in range(1, 200):
        ended = await sessions.open(_opener(index))
        await sessions.close(ended, MCP_BROWSER_END_CLOSED)

    assert sessions._secrets == {
        handle.cdp_url,
        SHARED_QUERY,
        handle.session_id,
        handle.live_view_url,
        handle.session.control_url.get_secret_value(),
    }
    assert "backend-session-199" not in sessions._secrets
    assert MCP_REDACTED in sessions.scrub_text(handle.cdp_url)
    assert MCP_REDACTED in sessions.scrub_text(SHARED_QUERY)
    assert len(sessions._secrets) <= 10
