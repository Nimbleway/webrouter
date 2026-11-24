"""A session's secrets are masked while it is open, and gone once it ends.

``BrowserSessions`` masks the values of the sessions it holds. Holding them for
the life of the server instead would keep the CDP URL and the backend's session
id of every session ever opened -- and would make masking slower with every one
of them. What this pins: only the open sessions' values are held, and the ones
of a closed session stop being masked because they are no longer there.
"""

from __future__ import annotations

from types import SimpleNamespace

import anyio
from pydantic import SecretStr

from web_router.constants import MCP_BROWSER_END_CLOSED, MCP_REDACTED
from web_router.exceptions import BrowserActionError, PageClosedError, WebRouterError
from web_router.mcp.browsers import BrowserSessions, BrowserToolError

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

    assert sessions._open_secrets == {
        handle.cdp_url,
        SHARED_QUERY,
        handle.session_id,
        handle.live_view_url,
        handle.session.control_url.get_secret_value(),
    }
    assert "backend-session-199" not in sessions._open_secrets
    assert MCP_REDACTED in sessions.scrub_text(handle.cdp_url)
    assert MCP_REDACTED in sessions.scrub_text(SHARED_QUERY)


async def test_ending_session_values_still_scrub_that_operations_error() -> None:
    sessions = BrowserSessions()
    lost_id = await sessions.open(_opener(7))
    lost = sessions._sessions[lost_id].handle

    async def _closed(_: object) -> None:
        raise PageClosedError(
            "fake",
            lost.session_id,
            "click",
            f"backend {lost.session_id} at {lost.cdp_url} view {lost.live_view_url}",
        )

    error: BrowserToolError | None = None
    try:
        await sessions.run(lost_id, "browser_click", _closed)
    except BrowserToolError as exc:
        error = exc
    assert error is not None
    message = str(error)
    assert MCP_REDACTED in message
    assert lost_id not in sessions._sessions
    for value in (lost.session_id, lost.cdp_url, lost.live_view_url):
        assert value not in message
        assert value not in sessions._open_secrets

    class _FailingRelease(_Handle):
        async def release(self) -> None:
            raise WebRouterError(f"cannot release {self.session_id} at {self.cdp_url} view {self.live_view_url}")

    async def open_failing() -> _Handle:
        return _FailingRelease(8)

    closing_id = await sessions.open(open_failing)
    closing = sessions._sessions[closing_id].handle
    error = None
    try:
        await sessions.close(closing_id, MCP_BROWSER_END_CLOSED)
    except BrowserToolError as exc:
        error = exc
    assert error is not None
    message = str(error)
    assert MCP_REDACTED in message
    assert closing_id not in sessions._sessions
    for value in (closing.session_id, closing.cdp_url, closing.live_view_url):
        assert value not in message
        assert value not in sessions._open_secrets


async def test_an_action_in_flight_when_close_starts_is_still_scrubbed() -> None:
    sessions = BrowserSessions()
    session_id = await sessions.open(_opener(0))
    handle = sessions._sessions[session_id].handle
    action_started = anyio.Event()
    let_action_fail = anyio.Event()
    close_started = anyio.Event()
    messages: list[str] = []

    async def _failing(_: object) -> None:
        action_started.set()
        await let_action_fail.wait()
        raise BrowserActionError(
            "fake",
            handle.session_id,
            "click",
            f"click failed on {handle.session_id} at {handle.cdp_url}",
        )

    async def _acting() -> None:
        try:
            await sessions.run(session_id, "browser_click", _failing)
        except BrowserToolError as exc:
            messages.append(str(exc))

    async def _closing() -> None:
        close_started.set()
        await sessions.close(session_id, MCP_BROWSER_END_CLOSED)

    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(_acting)
            await action_started.wait()
            group.start_soon(_closing)
            await close_started.wait()
            let_action_fail.set()

    assert len(messages) == 1
    message = messages[0]
    assert MCP_REDACTED in message
    assert handle.session_id not in message
    assert handle.cdp_url not in message


async def test_a_second_close_waiting_on_the_lock_still_scrubs_the_ending_session() -> None:
    sessions = BrowserSessions()
    action_started = anyio.Event()
    let_action_finish = anyio.Event()
    first_release_started = anyio.Event()
    let_first_release_finish = anyio.Event()
    messages: list[str] = []

    class _FailsOnEveryRelease(_Handle):
        calls = 0

        async def release(self) -> None:
            self.calls += 1
            if self.calls == 1:
                first_release_started.set()
                await let_first_release_finish.wait()
            raise WebRouterError(f"cannot release {self.session_id} at {self.cdp_url} view {self.live_view_url}")

    async def open_failing() -> _Handle:
        return _FailsOnEveryRelease(11)

    session_id = await sessions.open(open_failing)
    handle = sessions._sessions[session_id].handle

    async def _acting() -> None:
        async def _block(_: object) -> None:
            action_started.set()
            await let_action_finish.wait()

        await sessions.run(session_id, "browser_goto", _block)

    async def _closing() -> None:
        try:
            await sessions.close(session_id, MCP_BROWSER_END_CLOSED)
        except BrowserToolError as exc:
            messages.append(str(exc))

    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(_acting)
            await action_started.wait()
            # Both closes take their session reference before the first one can end it.
            group.start_soon(_closing)
            await anyio.sleep(0)
            group.start_soon(_closing)
            await anyio.sleep(0)
            let_action_finish.set()
            await first_release_started.wait()
            let_first_release_finish.set()

    assert len(messages) == 2
    for message in messages:
        assert MCP_REDACTED in message
        assert handle.session_id not in message
