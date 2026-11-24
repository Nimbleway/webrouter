"""``close()`` racing an action or another ``close()`` on the same session: released once, nothing unmasked."""

from __future__ import annotations

import anyio

from tests.mcp.test_browser_secret_retention import _Handle, _opener
from web_router.constants import MCP_BROWSER_END_CLOSED, MCP_REDACTED
from web_router.exceptions import BrowserActionError, WebRouterError
from web_router.mcp.browsers import BrowserSessions, BrowserToolError


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


async def test_a_second_close_waiting_on_the_lock_releases_once_and_leaks_nothing() -> None:
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

    assert handle.calls == 1  # the second close finds the session already ended
    assert len(messages) == 1
    assert MCP_REDACTED in messages[0]
    assert handle.session_id not in messages[0]
