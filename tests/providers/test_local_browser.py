"""Local browser provider tests.

The unit tests replace the browser process with a fake that writes what a real
Chromium writes -- the ``DevToolsActivePort`` file, or the "DevTools listening"
line -- so nothing is launched, and every temporary profile lands under pytest's
own ``tmp_path``. The ``browser``-marked tests at the bottom launch the real
installed browser, check its CDP endpoint answers, and check release really ends
the process; they skip when no browser is found. Run them with
``make test-browser``.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx
import pytest

from web_router import AsyncWebRouter, WebRouter
from web_router.constants import (
    BROWSER_TIMEOUT,
    ENV_LOCAL_BROWSER_PATH,
    ENV_PLAYWRIGHT_BROWSERS_PATH,
    LOCAL_BROWSER_RESERVED_SWITCHES,
    LOCAL_BROWSER_TEMP_PREFIX,
    LOCAL_DEVTOOLS_PORT_FILE,
)
from web_router.exceptions import ConfigurationError, ProviderError, UnsupportedParameterError
from web_router.providers import local
from web_router.providers.local import LocalBrowserProvider, find_browser
from web_router.types import BrowserRequest, Viewport

BROWSER_GUID = "4c837e26-20e7-431d-92a8-7307962392f9"
PORT = 61860


class _FakeProcess:
    """Stands in for the browser: announces itself the way the test asks, then waits to be stopped."""

    instances: ClassVar[list[_FakeProcess]] = []

    def __init__(
        self,
        command: list[str],
        *,
        announce: str,
        exit_code: int | None,
        stubborn: bool,
        undying: bool,
        port: int,
        **kwargs: Any,
    ) -> None:
        self.command = command
        self.pid = 4242
        self.returncode: int | None = None
        self.terminated = self.killed = False
        self.stubborn = stubborn or undying
        self.undying = undying
        # Each real browser mints its own id; the first launch keeps the recorded one.
        guid = BROWSER_GUID if not _FakeProcess.instances else f"{BROWSER_GUID[:-1]}{len(_FakeProcess.instances)}"
        profile = Path(next(arg for arg in command if arg.startswith("--user-data-dir=")).split("=", 1)[1])
        port_file = f"{port}\n/devtools/browser/{guid}\n"
        if announce == "port-file":
            profile.mkdir(parents=True)
            (profile / LOCAL_DEVTOOLS_PORT_FILE).write_text(port_file)
        elif announce == "late":
            profile.mkdir(parents=True)
            threading.Timer(0.3, (profile / LOCAL_DEVTOOLS_PORT_FILE).write_text, args=(port_file,)).start()
        elif announce == "stderr":
            kwargs["stdout"].write(f"DevTools listening on ws://127.0.0.1:{port}/devtools/browser/{guid}\n".encode())
        else:
            kwargs["stdout"].write(b"[FATAL] cannot open display\n")
        self.returncode = exit_code
        _FakeProcess.instances.append(self)

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        if not self.stubborn:
            self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        if not self.undying:
            self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired("browser", timeout or 0)
        return self.returncode


@pytest.fixture
def launch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """Point the provider at a fake executable and a fake process; returns a setter for the process's behaviour.

    Profiles go under ``tmp_path`` rather than the system temp dir, and the
    fixture fails a test that leaves one behind.
    """
    executable = tmp_path / "chrome"
    executable.write_text("")
    executable.chmod(0o755)
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(profiles))
    monkeypatch.setenv(ENV_LOCAL_BROWSER_PATH, str(executable))
    monkeypatch.setattr(local, "LOCAL_BROWSER_STARTUP_TIMEOUT", 0.2)
    _FakeProcess.instances = []
    behaviour: dict[str, Any] = {
        "announce": "port-file",
        "exit_code": None,
        "stubborn": False,
        "undying": False,
        "port": PORT,
    }

    def popen(command: list[str], **kwargs: Any) -> _FakeProcess:
        return _FakeProcess(command, **behaviour, **kwargs)

    monkeypatch.setattr(local.subprocess, "Popen", popen)
    yield behaviour
    leftovers = list(profiles.glob(f"{LOCAL_BROWSER_TEMP_PREFIX}*"))
    assert leftovers == [], f"a test left a profile behind: {leftovers}"


def _process() -> _FakeProcess:
    return _FakeProcess.instances[-1]


def _profile_root(process: _FakeProcess) -> Path:
    arg = next(arg for arg in process.command if arg.startswith("--user-data-dir="))
    return Path(arg.split("=", 1)[1]).parent


def _eventually(condition: Any, within: float = 2.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return bool(condition())


# -- Launching -------------------------------------------------------------------


def test_open_returns_the_browser_level_cdp_url_from_the_port_file(launch: Any) -> None:
    with LocalBrowserProvider().open(BrowserRequest()) as browser:
        assert browser.cdp_url == f"ws://127.0.0.1:{PORT}/devtools/browser/{BROWSER_GUID}"
        assert browser.session_id == BROWSER_GUID
        assert browser.status() == "running"


def test_open_falls_back_to_the_listening_line_the_browser_prints(launch: Any) -> None:
    launch["announce"] = "stderr"
    with LocalBrowserProvider().open(BrowserRequest()) as browser:
        assert browser.cdp_url == f"ws://127.0.0.1:{PORT}/devtools/browser/{BROWSER_GUID}"


def test_launch_uses_a_free_loopback_port_and_is_headless_by_default(launch: Any) -> None:
    with LocalBrowserProvider().open(BrowserRequest()):
        command = _process().command
    assert "--remote-debugging-port=0" in command
    assert "--remote-debugging-address=127.0.0.1" in command
    assert "--headless=new" in command


def test_headless_false_opens_a_window(launch: Any) -> None:
    with LocalBrowserProvider().open(BrowserRequest(headless=False)):
        command = _process().command
    assert not any(arg.startswith("--headless") for arg in command)


def test_provider_options_become_switches(launch: Any) -> None:
    request = BrowserRequest(
        provider_options={"lang": "en-US", "--disable-gpu": True, "mute-audio": False, "window-size": "800,600"}
    )
    with LocalBrowserProvider().open(request):
        command = _process().command
    assert "--lang=en-US" in command
    assert "--disable-gpu" in command
    assert "--window-size=800,600" in command
    assert not any("mute-audio" in arg for arg in command)


def _spellings(name: str) -> list[str]:
    return [name, f"--{name}", f"-{name}", f"--{name.upper()}", f"{name}=1"]


@pytest.mark.parametrize("key", [key for name in sorted(LOCAL_BROWSER_RESERVED_SWITCHES) for key in _spellings(name)])
def test_a_reserved_switch_is_refused_however_it_is_spelled(launch: Any, key: str) -> None:
    with pytest.raises(ConfigurationError, match="provider_options may not set"):
        LocalBrowserProvider().open(BrowserRequest(provider_options={key: "1"}))
    assert _FakeProcess.instances == []


def test_the_loopback_pin_and_origin_check_are_among_the_reserved_switches() -> None:
    assert {
        "remote-debugging-port",
        "remote-debugging-address",
        "remote-debugging-pipe",
        "remote-debugging-io-pipes",
        "remote-allow-origins",
    } <= LOCAL_BROWSER_RESERVED_SWITCHES


def test_no_sandbox_stays_allowed(launch: Any) -> None:
    with LocalBrowserProvider().open(BrowserRequest(provider_options={"no-sandbox": True})):
        assert "--no-sandbox" in _process().command


@pytest.mark.parametrize(
    "option",
    [
        {"stealth": "low"},
        {"stealth": "off"},
        {"proxy_country": "US"},
    ],
)
def test_options_a_local_browser_cannot_honour_are_rejected(launch: Any, option: dict[str, Any]) -> None:
    with pytest.raises(UnsupportedParameterError):
        LocalBrowserProvider().open(BrowserRequest(**option))
    assert _FakeProcess.instances == []


class _FakeDevTools:
    """A browser's DevTools websocket, answering each CDP call from ``answers`` and recording it.

    Sends an event before every answer, as a real browser may, so a client that
    takes the first frame for its reply fails here.
    """

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.paths: list[str] = []
        self._server = socket.create_server(("127.0.0.1", 0))
        self.port = self._server.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        with self._server, contextlib.suppress(OSError):
            connection, _ = self._server.accept()
            with connection:
                buffer = b""
                while b"\r\n\r\n" not in buffer:
                    buffer += connection.recv(4096)
                self.paths.append(buffer.split(b" ", 2)[1].decode())
                connection.sendall(
                    b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
                )
                while True:
                    header = connection.recv(2)
                    if len(header) < 2:
                        return
                    size = header[1] & 0x7F
                    if size == 126:
                        size = int.from_bytes(connection.recv(2), "big")
                    mask = connection.recv(4)
                    body = b""
                    while len(body) < size:
                        body += connection.recv(size - len(body))
                    message = json.loads(bytes(byte ^ mask[i % 4] for i, byte in enumerate(body)))
                    self.calls.append((message["method"], message["params"]))
                    answer = self.answers[message["method"]]
                    self._frame(connection, {"method": "Target.targetCreated", "params": {}})
                    self._frame(connection, {"id": message["id"], **answer})

    @staticmethod
    def _frame(connection: socket.socket, message: dict[str, Any]) -> None:
        payload = json.dumps(message).encode()
        length = bytes([len(payload)]) if len(payload) < 126 else bytes([126]) + len(payload).to_bytes(2, "big")
        connection.sendall(bytes([0x81]) + length + payload)


_PAGE_ANSWERS: dict[str, Any] = {
    "Target.getTargets": {
        "result": {"targetInfos": [{"targetId": "SW", "type": "service_worker"}, {"targetId": "P1", "type": "page"}]}
    },
    "Browser.getWindowForTarget": {"result": {"windowId": 7, "bounds": {}}},
    "Browser.setContentsSize": {"result": {}},
}


def test_viewport_sizes_the_page_of_the_first_page_target(launch: Any) -> None:
    devtools = _FakeDevTools(_PAGE_ANSWERS)
    launch["port"] = devtools.port
    with LocalBrowserProvider().open(BrowserRequest(viewport=Viewport(width=1440, height=900))):
        pass
    assert devtools.paths == [f"/devtools/browser/{BROWSER_GUID}"]
    assert devtools.calls == [
        ("Target.getTargets", {}),
        ("Browser.getWindowForTarget", {"targetId": "P1"}),
        ("Browser.setContentsSize", {"windowId": 7, "width": 1440, "height": 900}),
    ]
    assert not any(arg.startswith("--window-size") for arg in _process().command)


def test_a_browser_that_cannot_size_its_page_is_stopped_and_the_open_fails(launch: Any) -> None:
    answers = {
        **_PAGE_ANSWERS,
        "Browser.setContentsSize": {"error": {"code": -32601, "message": "'Browser.setContentsSize' wasn't found"}},
    }
    devtools = _FakeDevTools(answers)
    launch["port"] = devtools.port
    with pytest.raises(ProviderError, match=r"would not size its page to 800x600: .*wasn.t found"):
        LocalBrowserProvider().open(BrowserRequest(viewport=Viewport(width=800, height=600)))
    assert _process().terminated
    assert not _profile_root(_process()).exists()


def test_without_a_viewport_nothing_talks_to_devtools(launch: Any) -> None:
    devtools = _FakeDevTools(_PAGE_ANSWERS)
    launch["port"] = devtools.port
    with LocalBrowserProvider().open(BrowserRequest()):
        pass
    assert devtools.paths == []


@pytest.mark.parametrize("key", ["window-size", "--start-maximized", "KIOSK"])
def test_a_switch_that_sizes_the_window_is_refused_beside_viewport(launch: Any, key: str) -> None:
    with pytest.raises(ConfigurationError, match="set the size in one place only"):
        LocalBrowserProvider().open(
            BrowserRequest(viewport=Viewport(width=800, height=600), provider_options={key: "1"})
        )
    assert _FakeProcess.instances == []


@pytest.mark.parametrize("value", [False, None])
def test_a_sizing_switch_turned_off_does_not_conflict_with_viewport(launch: Any, value: Any) -> None:
    devtools = _FakeDevTools(_PAGE_ANSWERS)
    launch["port"] = devtools.port
    request = BrowserRequest(viewport=Viewport(width=800, height=600), provider_options={"window-size": value})
    with LocalBrowserProvider().open(request):
        command = _process().command
    assert not any(arg.startswith("--window-size") for arg in command)


def test_a_browser_that_exits_before_listening_raises_and_leaves_nothing_behind(launch: Any) -> None:
    launch.update(announce="nothing", exit_code=1)
    with pytest.raises(ProviderError, match="cannot open display"):
        LocalBrowserProvider().open(BrowserRequest())
    assert not _profile_root(_process()).exists()


def test_a_browser_that_never_listens_is_stopped_after_the_startup_timeout(launch: Any) -> None:
    launch["announce"] = "nothing"
    with pytest.raises(ProviderError, match="still silent"):
        LocalBrowserProvider().open(BrowserRequest())
    assert _process().terminated
    assert not _profile_root(_process()).exists()


async def test_the_event_loop_keeps_running_while_the_browser_starts(
    launch: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    launch["announce"] = "late"
    monkeypatch.setattr(local, "LOCAL_BROWSER_STARTUP_TIMEOUT", 5.0)
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    ticker = asyncio.create_task(tick())
    provider = LocalBrowserProvider()
    handle = await provider.aopen(BrowserRequest())
    ticker.cancel()
    await handle.release()
    # The browser took 0.3s to announce itself; a blocked loop would have ticked once.
    assert ticks >= 10


# -- Lifetime --------------------------------------------------------------------


def test_the_default_lifetime_applies_and_arms_a_timer(launch: Any) -> None:
    seen: list[BrowserRequest] = []
    provider = LocalBrowserProvider()
    original = provider._open

    def spy(request: BrowserRequest) -> Any:
        seen.append(request)
        return original(request)

    provider._open = spy  # type: ignore[method-assign]
    browser = provider.open(BrowserRequest())
    timer = provider._sessions[browser.session_id].timer
    assert seen[0].timeout == BROWSER_TIMEOUT
    assert timer is not None and timer.interval == BROWSER_TIMEOUT and timer.daemon

    browser.release()
    timer.join(1)
    assert not timer.is_alive()


class _ManualTimer:
    """Stands in for ``threading.Timer``: armed like one, but fires only when the test says so."""

    armed: ClassVar[list[_ManualTimer]] = []

    def __init__(self, interval: float, function: Any, args: tuple[Any, ...] = ()) -> None:
        self.interval = interval
        self.function = function
        self.args = args
        self.daemon = False
        self.started = self.cancelled = False
        _ManualTimer.armed.append(self)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        """Run the callback on the test's own thread, as the real timer's thread would once the interval ran out."""
        assert self.started
        self.function(*self.args)


@pytest.fixture
def manual_timer(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The timer the provider arms, driven by hand, so a lifetime runs out exactly when the test says."""
    _ManualTimer.armed = []
    monkeypatch.setattr(local.threading, "Timer", _ManualTimer)
    yield lambda: _ManualTimer.armed[-1]


def test_a_session_whose_lifetime_runs_out_is_stopped_and_reports_expired(launch: Any, manual_timer: Any) -> None:
    provider = LocalBrowserProvider()
    browser = provider.open(BrowserRequest(timeout=0.05))
    root = _profile_root(_process())
    timer = manual_timer()
    assert timer.interval == 0.05 and timer.daemon
    assert provider.status(browser.session_id) == "running"

    timer.fire()

    assert provider.status(browser.session_id) == "expired"
    assert _process().terminated
    assert not root.exists()
    browser.release()
    assert browser.released


def test_a_released_session_is_not_ended_again_by_its_timer(launch: Any, manual_timer: Any) -> None:
    provider = LocalBrowserProvider()
    browser = provider.open(BrowserRequest(timeout=0.1))
    timer = manual_timer()
    provider.release(browser.session_id)
    assert timer.cancelled

    # A timer that had already begun firing as the release landed must still be a no-op.
    timer.fire()

    assert provider.status(browser.session_id) == "released"


# -- Releasing -------------------------------------------------------------------


def test_release_stops_the_process_and_removes_the_profile(launch: Any) -> None:
    browser = LocalBrowserProvider().open(BrowserRequest())
    root = _profile_root(_process())
    assert root.exists()

    browser.release()

    assert _process().terminated
    assert not _process().killed
    assert not root.exists()
    assert browser.status() == "released"


def test_a_browser_that_ignores_terminate_is_killed(launch: Any) -> None:
    launch["stubborn"] = True
    LocalBrowserProvider().open(BrowserRequest()).release()
    assert _process().terminated
    assert _process().killed


def test_the_profile_is_removed_even_when_the_browser_survives_kill(launch: Any) -> None:
    launch["undying"] = True
    provider = LocalBrowserProvider()
    browser = provider.open(BrowserRequest())
    root = _profile_root(_process())

    with pytest.raises(ProviderError):
        provider.release(browser.session_id)

    assert _process().killed
    assert not root.exists()
    assert provider.status(browser.session_id) == "running"


def _rmtree_failing(monkeypatch: pytest.MonkeyPatch, times: int) -> list[Path]:
    """Make removing a profile fail ``times`` times, as a file held open on Windows would, then work."""
    attempts: list[Path] = []
    real = local.shutil.rmtree

    def rmtree(path: Any, *args: Any, **kwargs: Any) -> None:
        attempts.append(Path(path))
        if len(attempts) <= times:
            raise PermissionError(13, "Permission denied", str(path))
        real(path, *args, **kwargs)

    monkeypatch.setattr(local.shutil, "rmtree", rmtree)
    return attempts


def test_a_profile_that_could_not_be_removed_is_warned_about_and_retried_on_release(
    launch: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = LocalBrowserProvider()
    browser = provider.open(BrowserRequest())
    root = _profile_root(_process())
    attempts = _rmtree_failing(monkeypatch, times=1)

    with pytest.warns(RuntimeWarning, match="could not be removed"):
        browser.release()

    assert _process().terminated, "the process is stopped before the profile is touched"
    assert root.exists()
    assert browser.released and browser.status() == "released"
    provider.release(browser.session_id)
    assert not root.exists()
    assert attempts == [root, root]
    assert browser.session_id not in provider._sessions


def test_a_profile_left_behind_is_retried_by_close(launch: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = LocalBrowserProvider()
    browser = provider.open(BrowserRequest())
    root = _profile_root(_process())
    _rmtree_failing(monkeypatch, times=2)

    with pytest.warns(RuntimeWarning, match=re.escape(str(root))):
        browser.release()
    with pytest.warns(RuntimeWarning, match="could not be removed"):
        provider.close()
    assert root.exists(), "still failing, so still tracked"
    provider.close()

    assert not root.exists()
    assert provider._sessions == {}


def test_close_stops_the_rest_when_one_browser_will_not_stop(launch: Any) -> None:
    provider = LocalBrowserProvider()
    launch["undying"] = True
    provider.open(BrowserRequest())
    launch["undying"] = False
    provider.open(BrowserRequest())
    stuck, fine = _FakeProcess.instances

    with pytest.raises(ProviderError, match="could not fully stop 1"):
        provider.close()

    assert fine.terminated and fine.returncode is not None
    assert not _profile_root(stuck).exists() and not _profile_root(fine).exists()
    # The one still running is still tracked, so the exit hook tries it again.
    assert [launched.process for launched in provider._sessions.values()] == [stuck]


def test_the_exit_hook_stops_the_rest_when_one_browser_will_not_stop(launch: Any) -> None:
    provider = LocalBrowserProvider()
    launch["undying"] = True
    provider.open(BrowserRequest())
    launch["undying"] = False
    provider.open(BrowserRequest())
    stuck, fine = _FakeProcess.instances

    provider._finalizer()

    assert fine.returncode is not None
    assert not _profile_root(stuck).exists() and not _profile_root(fine).exists()


def test_releasing_twice_is_a_no_op(launch: Any) -> None:
    provider = LocalBrowserProvider()
    browser = provider.open(BrowserRequest())
    provider.release(browser.session_id)
    provider.release(browser.session_id)
    assert provider.status(browser.session_id) == "released"


def test_the_memory_of_ended_sessions_is_bounded(launch: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local, "LOCAL_BROWSER_ENDED_MEMORY", 2)
    provider = LocalBrowserProvider()
    ids = [provider.open(BrowserRequest()) for _ in range(3)]
    for browser in ids:
        browser.release()
    assert list(provider._ended) == [ids[1].session_id, ids[2].session_id]


@pytest.mark.parametrize(("code", "state"), [(0, "released"), (1, "failed")])
def test_a_browser_that_ended_on_its_own_reports_how(launch: Any, code: int, state: str) -> None:
    provider = LocalBrowserProvider()
    browser = provider.open(BrowserRequest())
    _process().returncode = code
    assert browser.status() == state
    browser.release()


def test_a_session_this_provider_never_opened_is_an_error(launch: Any) -> None:
    with pytest.raises(ProviderError, match="no session"):
        LocalBrowserProvider().status("somebody-elses")
    with pytest.raises(ProviderError, match="no session"):
        LocalBrowserProvider().release("somebody-elses")


def test_closing_the_router_stops_every_browser_it_launched(launch: Any) -> None:
    router = WebRouter("local")
    router.open_browser()
    router.open_browser()
    router.close()
    assert len(_FakeProcess.instances) == 2
    assert all(process.terminated for process in _FakeProcess.instances)


async def test_the_async_path_opens_and_releases(launch: Any) -> None:
    router = AsyncWebRouter("local")
    async with await router.open_browser() as browser:
        assert await browser.status() == "running"
    assert _process().terminated
    assert await browser.status() == "released"
    await router.aclose()


# -- Finding the browser ---------------------------------------------------------


@pytest.fixture
def nowhere(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A machine with no browser installed and an empty Playwright cache."""
    monkeypatch.delenv(ENV_LOCAL_BROWSER_PATH, raising=False)
    monkeypatch.setenv(ENV_PLAYWRIGHT_BROWSERS_PATH, str(tmp_path / "ms-playwright"))
    monkeypatch.setattr(local, "_INSTALLED", {})
    return tmp_path


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    path.chmod(0o755)
    return path


def test_no_browser_anywhere_raises_configuration_error_with_the_fix(nowhere: Path) -> None:
    with pytest.raises(ConfigurationError, match="uvx playwright install chromium"):
        find_browser()


def test_a_missing_browser_surfaces_from_open_unwrapped(nowhere: Path) -> None:
    with pytest.raises(ConfigurationError), WebRouter("local").open_browser():
        pass


def test_an_explicit_path_wins_and_a_wrong_one_is_not_papered_over(nowhere: Path) -> None:
    chrome = _executable(nowhere / "my-chrome")
    assert find_browser(str(chrome)) == str(chrome)
    with pytest.raises(ConfigurationError, match="executable_path"):
        find_browser(str(nowhere / "typo"))


def test_the_path_can_come_from_provider_config(nowhere: Path) -> None:
    chrome = _executable(nowhere / "configured")
    router = WebRouter("local", provider_config={"local": {"executable_path": str(chrome)}})
    provider = router.get_browser_provider()
    assert isinstance(provider, LocalBrowserProvider)
    assert provider.executable_path == str(chrome)


def test_the_environment_variable_is_read_next(nowhere: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chrome = _executable(nowhere / "from-env")
    monkeypatch.setenv(ENV_LOCAL_BROWSER_PATH, str(chrome))
    assert find_browser() == str(chrome)
    monkeypatch.setenv(ENV_LOCAL_BROWSER_PATH, str(nowhere / "gone"))
    with pytest.raises(ConfigurationError, match=ENV_LOCAL_BROWSER_PATH):
        find_browser()


def test_the_newest_playwright_chromium_is_found(nowhere: Path) -> None:
    cache = nowhere / "ms-playwright"
    _executable(cache / "chromium-1100" / "chrome-linux" / "chrome")
    newest = _executable(cache / "chromium-1200" / "chrome-linux64" / "chrome")
    _executable(cache / "chromium_headless_shell-1300" / "chrome-linux" / "headless_shell")
    assert find_browser() == str(newest)


def test_an_installed_browser_is_preferred_to_playwrights(nowhere: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    installed = _executable(nowhere / "Google Chrome")
    _executable(nowhere / "ms-playwright" / "chromium-1200" / "chrome-linux64" / "chrome")
    monkeypatch.setattr(local, "_INSTALLED", {sys.platform: lambda: iter([str(installed)])})
    assert find_browser() == str(installed)


def test_windows_looks_under_each_install_root(nowhere: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chrome = _executable(nowhere / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe")
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(nowhere / "Local"))
    assert list(local._windows()) == [str(chrome), str(nowhere / "Local" / "Chromium" / "Application" / "chrome.exe")]


# -- Against the real browser ----------------------------------------------------


def _installed_browser() -> str | None:
    try:
        return find_browser()
    except ConfigurationError:
        return None


needs_browser = pytest.mark.skipif(_installed_browser() is None, reason="no Chromium-family browser is installed")


def _http_base(cdp_url: str) -> str:
    # Plain HTTP beside the websocket: the same port serves /json/version and /json/list.
    return f"http://{urlsplit(cdp_url).netloc}"


def _version(cdp_url: str) -> dict[str, Any]:
    response = httpx.get(f"{_http_base(cdp_url)}/json/version", timeout=10)
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    return payload


def _evaluate(cdp_url: str, expression: str) -> Any:
    """Run ``expression`` in the session's first page over CDP, with a bare stdlib websocket."""
    targets = httpx.get(f"{_http_base(cdp_url)}/json/list", timeout=10).json()
    page = urlsplit(next(target for target in targets if target["type"] == "page")["webSocketDebuggerUrl"])
    with socket.create_connection((page.hostname, page.port), timeout=10) as sock:
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(
            f"GET {page.path} HTTP/1.1\r\nHost: {page.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            buffer += sock.recv(4096)
        head, buffer = buffer.split(b"\r\n\r\n", 1)
        assert b" 101 " in head.split(b"\r\n", 1)[0]

        message = {"id": 1, "method": "Runtime.evaluate", "params": {"expression": expression, "returnByValue": True}}
        payload = json.dumps(message).encode()
        mask = os.urandom(4)
        size = len(payload)
        length = bytes([0x80 | size]) if size < 126 else bytes([0x80 | 126]) + size.to_bytes(2, "big")
        sock.sendall(bytes([0x81]) + length + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

        def read(count: int) -> bytes:
            nonlocal buffer
            while len(buffer) < count:
                buffer += sock.recv(65536)
            chunk, buffer = buffer[:count], buffer[count:]
            return chunk

        while True:
            size = read(2)[1] & 0x7F
            if size == 126:
                size = int.from_bytes(read(2), "big")
            elif size == 127:
                size = int.from_bytes(read(8), "big")
            reply = json.loads(read(size))
            if reply.get("id") == 1:
                return reply["result"]["result"]["value"]


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


@pytest.mark.browser
@needs_browser
def test_a_real_browser_serves_cdp_until_released() -> None:
    with WebRouter("local").open_browser() as browser:
        version = _version(browser.cdp_url)
        assert version["webSocketDebuggerUrl"] == browser.cdp_url
        assert browser.status() == "running"
        pid = browser.session.raw["pid"]  # type: ignore[index]
        profile = Path(browser.session.raw["user_data_dir"])  # type: ignore[index]
        assert profile.exists()

    assert _gone(pid)
    assert not profile.parent.exists()
    with pytest.raises(httpx.TransportError):
        _version(browser.cdp_url)


@pytest.mark.browser
@needs_browser
async def test_a_real_browser_opens_and_releases_through_the_async_router() -> None:
    router = AsyncWebRouter("local")
    async with await router.open_browser() as browser:
        assert _version(browser.cdp_url)["webSocketDebuggerUrl"] == browser.cdp_url
        pid = browser.session.raw["pid"]  # type: ignore[index]
    assert _gone(pid)
    await router.aclose()


@pytest.mark.browser
@needs_browser
def test_two_real_sessions_get_their_own_port_and_profile() -> None:
    router = WebRouter("local")
    first, second = router.open_browser(), router.open_browser()
    try:
        assert urlsplit(first.cdp_url).port != urlsplit(second.cdp_url).port
        assert first.session.raw["user_data_dir"] != second.session.raw["user_data_dir"]  # type: ignore[index]
    finally:
        router.close()
    assert first.released and second.released


@pytest.mark.browser
@needs_browser
def test_a_real_session_is_stopped_when_its_lifetime_runs_out() -> None:
    router = WebRouter("local")
    browser = router.open_browser(timeout=1)
    pid = browser.session.raw["pid"]  # type: ignore[index]
    assert browser.status() == "running"

    assert _eventually(lambda: browser.status() == "expired", within=10)
    # The state is recorded as the stop begins, so a release racing the timer is a no-op.
    assert _eventually(lambda: _gone(pid), within=10)
    router.close()


@pytest.mark.browser
@needs_browser
def test_window_size_is_the_outer_window_which_is_why_viewport_does_not_use_it() -> None:
    """Pins the measurement behind sizing over CDP: headless Chrome still draws a frame around the page."""
    sizes: list[list[int]] = []
    with WebRouter("local").open_browser(provider_options={"window-size": "800,600"}) as browser:
        # A page asked the moment the browser starts can report 0x0 before its window is laid out.
        assert _eventually(
            lambda: (
                sizes.append(_evaluate(browser.cdp_url, "[innerWidth, innerHeight, outerWidth, outerHeight]"))
                or sizes[-1][2] > 0
            ),
            within=10,
        )
    inner_width, inner_height, outer_width, outer_height = sizes[-1]
    assert (outer_width, outer_height) == (800, 600)
    assert inner_width == 800
    assert inner_height < 600


@pytest.mark.browser
@needs_browser
def test_a_real_session_renders_a_page_of_exactly_the_viewport() -> None:
    with WebRouter("local").open_browser(viewport=Viewport(width=1440, height=900)) as browser:
        assert _evaluate(browser.cdp_url, "[innerWidth, innerHeight]") == [1440, 900]


@pytest.mark.browser
@needs_browser
def test_a_browser_left_open_is_stopped_when_python_exits(tmp_path: Path) -> None:
    script = textwrap.dedent(
        """
        import json, warnings
        from web_router import WebRouter
        warnings.simplefilter("ignore", ResourceWarning)
        browser = WebRouter("local").open_browser()
        print(json.dumps(browser.session.raw), flush=True)
        """
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60, check=True)
    raw = json.loads(result.stdout.strip().splitlines()[-1])

    assert _eventually(lambda: _gone(raw["pid"]), within=10)
    assert not Path(raw["user_data_dir"]).parent.exists()
