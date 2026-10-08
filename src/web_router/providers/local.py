"""Local provider: a Chromium-family browser launched on this machine.

Browser only, and keyless -- the tier Jina Reader is for extract. Needs nothing
beyond the standard library: the browser is started as a subprocess with its
DevTools endpoint on a port it picks itself, sized over a bare CDP websocket
when a viewport is asked for, and ended by stopping the process.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import warnings
import weakref
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

from pydantic import SecretStr

from web_router.constants import (
    ENV_LOCAL_BROWSER_PATH,
    ENV_PLAYWRIGHT_BROWSERS_PATH,
    LOCAL_BROWSER_ARGS,
    LOCAL_BROWSER_ENDED_MEMORY,
    LOCAL_BROWSER_HEADLESS_ARG,
    LOCAL_BROWSER_KILL_TIMEOUT,
    LOCAL_BROWSER_LINUX_COMMANDS,
    LOCAL_BROWSER_LOG_FILE,
    LOCAL_BROWSER_LOG_TAIL,
    LOCAL_BROWSER_MAC_PATHS,
    LOCAL_BROWSER_PATH_OPTION,
    LOCAL_BROWSER_PROFILE_DIR,
    LOCAL_BROWSER_RESERVED_SWITCHES,
    LOCAL_BROWSER_START_URL,
    LOCAL_BROWSER_STARTUP_POLL,
    LOCAL_BROWSER_STARTUP_TIMEOUT,
    LOCAL_BROWSER_SWITCH_ARG,
    LOCAL_BROWSER_SWITCH_VALUE_ARG,
    LOCAL_BROWSER_TEMP_PREFIX,
    LOCAL_BROWSER_TERMINATE_TIMEOUT,
    LOCAL_BROWSER_USER_DATA_ARG,
    LOCAL_BROWSER_VIEWPORT_SWITCHES,
    LOCAL_BROWSER_WINDOWS_PATHS,
    LOCAL_BROWSER_WINDOWS_ROOTS,
    LOCAL_CDP_HANDSHAKE,
    LOCAL_CDP_HOST,
    LOCAL_CDP_TIMEOUT,
    LOCAL_CDP_URL,
    LOCAL_DEVTOOLS_LISTENING_PATTERN,
    LOCAL_DEVTOOLS_PORT_FILE,
    MSG_LOCAL_BAD_PATH,
    MSG_LOCAL_CDP_CLOSED,
    MSG_LOCAL_CLEANUP_FAILED,
    MSG_LOCAL_DID_NOT_START,
    MSG_LOCAL_EXITED,
    MSG_LOCAL_LOG_TAIL,
    MSG_LOCAL_NO_BROWSER,
    MSG_LOCAL_PROFILE_LEFT,
    MSG_LOCAL_RESERVED_SWITCH,
    MSG_LOCAL_STARTUP_TIMEOUT,
    MSG_LOCAL_UNKNOWN_SESSION,
    MSG_LOCAL_VIEWPORT_FAILED,
    MSG_LOCAL_VIEWPORT_NO_PAGE,
    MSG_LOCAL_VIEWPORT_SWITCH,
    PLAYWRIGHT_CACHE_BY_PLATFORM,
    PLAYWRIGHT_CHROMIUM_DIR_GLOB,
    PLAYWRIGHT_CHROMIUM_EXECUTABLES,
    PROVIDER_LOCAL,
)
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.providers.base import BrowserProvider
from web_router.types import BrowserRequest, BrowserSession, BrowserState, Viewport

_LISTENING = re.compile(LOCAL_DEVTOOLS_LISTENING_PATTERN)


@dataclass
class _Launched:
    process: subprocess.Popen[bytes]
    root: Path
    timer: threading.Timer | None = None


class LocalBrowserProvider(BrowserProvider):
    """Launches a Chromium-family browser on this machine and hands back its CDP endpoint.

    Each session is its own browser process with a fresh, temporary profile,
    so nothing carries over from one session to the next or from the browser
    you use yourself. Its DevTools endpoint listens on ``127.0.0.1`` only.
    Releasing it stops the process -- asking politely first, then killing it --
    and deletes the profile.

    The browser is found in this order: ``executable_path`` from
    ``provider_config``, then the ``CHROME_PATH`` environment variable, then the
    usual install locations for Google Chrome and Chromium on this OS, then the
    Chromium ``playwright install chromium`` downloads. A path given explicitly
    that holds no executable raises rather than falling through, so a typo is
    not quietly answered by a different browser.

    Maps ``headless`` -- on unless it is ``False`` -- ``timeout`` and
    ``viewport``. A timer releases the session when its lifetime runs out,
    after which it reports ``expired``, and like every backend with a lifetime
    it gets :data:`~web_router.constants.BROWSER_TIMEOUT` when none is
    set. ``viewport`` is applied once the browser listens, with CDP's
    ``Browser.setContentsSize``, which resizes the window around a page of
    exactly that size; ``--window-size`` could not do it, because it sizes the
    outer window and even headless Chrome draws its page inside a frame --
    800x600 renders an 800x513 page. A browser too old to have that command
    fails the open rather than handing back a page of another size. A stock
    local browser has no stealth, proxy or CAPTCHA solving.

    ``provider_options`` become command-line switches, untranslated:
    ``{"lang": "en-US"}`` is ``--lang=en-US``, ``True`` sends the bare switch
    and ``False`` or ``None`` sends nothing. The switches that locate the
    session, keep it on loopback or carry a normalized option are refused,
    however they are spelled, and so are the ones that size the window when
    ``viewport`` is set.

    :meth:`aopen` launches the browser on a worker thread so the event loop
    keeps running. Cancelling it cannot stop that thread: the browser it
    launches is still recorded here, and is stopped by :meth:`close` or, at the
    latest, when this process exits.
    """

    name: ClassVar[str] = PROVIDER_LOCAL
    supported_parameters: ClassVar[frozenset[str]] = frozenset({"headless", "timeout", "viewport"})

    def __init__(self, *, executable_path: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.executable_path = executable_path
        self._sessions: dict[str, _Launched] = {}
        self._ended: OrderedDict[str, BrowserState] = OrderedDict()
        self._lock = threading.Lock()
        # A browser outlives the Python process that started it, so one left
        # open is stopped at exit rather than lingering headless and unseen.
        self._finalizer = weakref.finalize(self, _stop_all, self._sessions, self._lock)

    def _open(self, request: BrowserRequest) -> BrowserSession:
        executable = find_browser(self.executable_path)
        arguments = _arguments(request)
        root = Path(tempfile.mkdtemp(prefix=LOCAL_BROWSER_TEMP_PREFIX))
        profile = root / LOCAL_BROWSER_PROFILE_DIR
        try:
            process = _launch([executable, LOCAL_BROWSER_USER_DATA_ARG.format(path=profile), *arguments], root)
        except BaseException:
            shutil.rmtree(root, ignore_errors=True)
            raise
        launched = _Launched(process, root)
        try:
            port, path = self._wait_for_devtools(process, root, executable)
            if request.viewport is not None:
                _size_page(port, path, request.viewport, executable)
        except BaseException:
            _warn_if_left(root, _end(launched))
            raise

        session_id = path.rsplit("/", 1)[-1]
        if request.timeout is not None:
            launched.timer = threading.Timer(request.timeout, self._end_session, args=(session_id, "expired"))
            launched.timer.daemon = True
        with self._lock:
            self._sessions[session_id] = launched
        if launched.timer is not None:
            launched.timer.start()
        return BrowserSession(
            provider=self.name,
            session_id=session_id,
            cdp_url=SecretStr(LOCAL_CDP_URL.format(port=port, path=path)),
            raw={"pid": process.pid, "executable": executable, "port": port, "user_data_dir": str(profile)},
        )

    async def _aopen(self, request: BrowserRequest) -> BrowserSession:
        return await asyncio.to_thread(self._open, request)

    def _release(self, session_id: str) -> None:
        if self._end_session(session_id, "released"):
            return
        with self._lock:
            known = session_id in self._ended
        if not known:
            raise ProviderError(self.name, MSG_LOCAL_UNKNOWN_SESSION.format(session_id=session_id))

    async def _arelease(self, session_id: str) -> None:
        await asyncio.to_thread(self._release, session_id)

    def _fetch(self, session_id: str) -> BrowserState:
        with self._lock:
            ended = self._ended.get(session_id)
            launched = self._sessions.get(session_id)
        if ended is not None:
            return ended
        if launched is None:
            raise ProviderError(self.name, MSG_LOCAL_UNKNOWN_SESSION.format(session_id=session_id))
        code = launched.process.poll()
        if code is None:
            return "running"
        # Ended without being released: closed by hand if it exited cleanly, crashed if not.
        return "released" if code == 0 else "failed"

    async def _afetch(self, session_id: str) -> BrowserState:
        return await asyncio.to_thread(self._fetch, session_id)

    def close(self) -> None:
        """Stop every browser this provider launched that is still open.

        One that will not stop does not keep the rest running: each is tried,
        and the failures are reported together afterwards.
        """
        with self._lock:
            open_ids = list(self._sessions)
        errors: list[Exception] = []
        for session_id in open_ids:
            try:
                self._end_session(session_id, "released")
            except Exception as exc:
                errors.append(exc)
        if errors:
            message = MSG_LOCAL_CLEANUP_FAILED.format(count=len(errors), errors="; ".join(map(str, errors)))
            raise ProviderError(self.name, message, original=errors[0]) from errors[0]

    async def aclose(self) -> None:
        """Async counterpart of :meth:`close`."""
        await asyncio.to_thread(self.close)

    def _end_session(self, session_id: str, state: BrowserState) -> bool:
        """Stop a session's browser and remember how it ended. False if it was not open."""
        with self._lock:
            launched = self._sessions.pop(session_id, None)
            if launched is None:
                return False
            self._ended[session_id] = state
            while len(self._ended) > LOCAL_BROWSER_ENDED_MEMORY:
                self._ended.popitem(last=False)
        try:
            leftover = _end(launched)
        except BaseException:
            # A browser that survived being killed is still running, and saying
            # otherwise would let release() report success over a live process.
            with self._lock:
                self._ended.pop(session_id, None)
                self._sessions[session_id] = launched
            raise
        if leftover is not None:
            # Warned, not raised: the process is stopped, so the session bills
            # and runs no more, and the base class would report it ended over a
            # raise anyway. Kept in the table, so the next release() or close()
            # -- or, at the latest, this process exiting -- tries the removal again.
            with self._lock:
                self._sessions[session_id] = launched
            _warn_if_left(launched.root, leftover)
        return True

    def _wait_for_devtools(self, process: subprocess.Popen[bytes], root: Path, executable: str) -> tuple[int, str]:
        deadline = time.monotonic() + LOCAL_BROWSER_STARTUP_TIMEOUT
        while True:
            found = _read_port_file(root / LOCAL_BROWSER_PROFILE_DIR) or _read_log(root)
            if found is not None:
                return found
            code = process.poll()
            if code is not None:
                raise self._did_not_start(executable, root, MSG_LOCAL_EXITED.format(code=code))
            if time.monotonic() >= deadline:
                raise self._did_not_start(
                    executable, root, MSG_LOCAL_STARTUP_TIMEOUT.format(timeout=LOCAL_BROWSER_STARTUP_TIMEOUT)
                )
            time.sleep(LOCAL_BROWSER_STARTUP_POLL)

    def _did_not_start(self, executable: str, root: Path, detail: str) -> ProviderError:
        tail = _log_text(root)[-LOCAL_BROWSER_LOG_TAIL:].strip()
        if tail:
            detail = MSG_LOCAL_LOG_TAIL.format(detail=detail, tail=tail)
        return ProviderError(self.name, MSG_LOCAL_DID_NOT_START.format(path=executable, detail=detail))


def find_browser(explicit: str | None = None) -> str:
    """The Chromium-family executable the ``local`` provider would launch.

    Raises:
        ConfigurationError: if ``explicit`` or ``CHROME_PATH`` names no
            executable, or if none is installed where one is looked for.
    """
    configured = (
        (explicit, LOCAL_BROWSER_PATH_OPTION),
        (os.environ.get(ENV_LOCAL_BROWSER_PATH), ENV_LOCAL_BROWSER_PATH),
    )
    for value, source in configured:
        if value:
            found = shutil.which(os.path.expanduser(value))
            if found is None:
                raise ConfigurationError(MSG_LOCAL_BAD_PATH.format(path=value, source=source))
            return found
    for candidate in _candidates():
        found = shutil.which(candidate)
        if found is not None:
            return found
    raise ConfigurationError(
        MSG_LOCAL_NO_BROWSER.format(env_var=ENV_LOCAL_BROWSER_PATH, option=LOCAL_BROWSER_PATH_OPTION)
    )


def _candidates() -> Iterator[str]:
    yield from _INSTALLED.get(sys.platform, _nothing)()
    yield from _playwright_chromium()


def _mac() -> Iterator[str]:
    return (os.path.expanduser(path) for path in LOCAL_BROWSER_MAC_PATHS)


def _linux() -> Iterator[str]:
    return iter(LOCAL_BROWSER_LINUX_COMMANDS)


def _windows() -> Iterator[str]:
    for root in LOCAL_BROWSER_WINDOWS_ROOTS:
        base = os.environ.get(root)
        if base:
            yield from (str(Path(base, relative)) for relative in LOCAL_BROWSER_WINDOWS_PATHS)


def _nothing() -> Iterator[str]:
    return iter(())


_INSTALLED: dict[str, Callable[[], Iterator[str]]] = {"darwin": _mac, "linux": _linux, "win32": _windows}


def _playwright_chromium() -> Iterator[str]:
    cache = os.environ.get(ENV_PLAYWRIGHT_BROWSERS_PATH) or PLAYWRIGHT_CACHE_BY_PLATFORM.get(sys.platform)
    if not cache:
        return
    root = Path(os.path.expanduser(cache))
    if not root.is_dir():
        return
    # Newest revision first: an older one left behind by a previous install may no longer start.
    for revision in sorted(root.glob(PLAYWRIGHT_CHROMIUM_DIR_GLOB), key=_revision, reverse=True):
        yield from (str(revision / relative) for relative in PLAYWRIGHT_CHROMIUM_EXECUTABLES)


def _revision(directory: Path) -> int:
    number = directory.name.rpartition("-")[2]
    return int(number) if number.isdigit() else -1


def _arguments(request: BrowserRequest) -> list[str]:
    arguments = list(LOCAL_BROWSER_ARGS)
    if request.headless is not False:
        arguments.append(LOCAL_BROWSER_HEADLESS_ARG)
    if request.viewport is not None:
        emitted = {_switch_name(key) for key, value in request.provider_options.items() if _emits(value)}
        sizing = sorted(emitted & LOCAL_BROWSER_VIEWPORT_SWITCHES)
        if sizing:
            raise ConfigurationError(MSG_LOCAL_VIEWPORT_SWITCH.format(names=", ".join(sizing)))
    arguments.extend(_switches(request.provider_options))
    arguments.append(LOCAL_BROWSER_START_URL)
    return arguments


def _switch_name(key: str) -> str:
    # Chrome reads `-x`, `--x` and `--x=1` alike, and matches case-insensitively on Windows.
    return key.lstrip("-").split("=", 1)[0].lower()


def _emits(value: Any) -> bool:
    # `False` and `None` switch an option off by leaving it out of the command.
    return value is not None and value is not False


def _switches(options: dict[str, Any]) -> list[str]:
    reserved = sorted({_switch_name(key) for key in options} & LOCAL_BROWSER_RESERVED_SWITCHES)
    if reserved:
        raise ConfigurationError(MSG_LOCAL_RESERVED_SWITCH.format(names=", ".join(reserved)))
    switches = []
    for key, value in options.items():
        if not _emits(value):
            continue
        template = LOCAL_BROWSER_SWITCH_ARG if value is True else LOCAL_BROWSER_SWITCH_VALUE_ARG
        switches.append(template.format(name=key.lstrip("-"), value=value))
    return switches


def _launch(command: list[str], root: Path) -> subprocess.Popen[bytes]:
    # Output goes to a file rather than a pipe: a pipe nobody drains fills up
    # and stalls the browser, and the file is still there to quote on failure.
    with open(root / LOCAL_BROWSER_LOG_FILE, "wb") as log:
        return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)


def _size_page(port: int, path: str, viewport: Viewport, executable: str) -> None:
    """Resize the browser's window around a page of exactly ``viewport``, over its CDP endpoint."""
    try:
        with _CdpConnection(port, path) as cdp:
            page = cdp.first_page()
            window = cdp.call("Browser.getWindowForTarget", {"targetId": page})["windowId"]
            cdp.call(
                "Browser.setContentsSize", {"windowId": window, "width": viewport.width, "height": viewport.height}
            )
    except (OSError, ValueError, KeyError, _CdpError) as exc:
        detail = MSG_LOCAL_VIEWPORT_FAILED.format(
            path=executable, width=viewport.width, height=viewport.height, detail=exc
        )
        raise ProviderError(PROVIDER_LOCAL, detail, original=exc) from exc


class _CdpError(Exception):
    pass


class _CdpConnection:
    """The few CDP calls sizing a page takes, over a stdlib websocket to the browser's loopback endpoint.

    Only what Chrome sends back is read: unfragmented frames, unmasked, events
    skipped on the way to the answer. Not a general client, so opening a
    session never needs Playwright.
    """

    def __init__(self, port: int, path: str) -> None:
        self._deadline = time.monotonic() + LOCAL_CDP_TIMEOUT
        self._sock = socket.create_connection((LOCAL_CDP_HOST, port), timeout=LOCAL_CDP_TIMEOUT)
        self._buffer = b""
        self._last_id = 0
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            self._sock.sendall(LOCAL_CDP_HANDSHAKE.format(path=path, host=LOCAL_CDP_HOST, port=port, key=key).encode())
            while b"\r\n\r\n" not in self._buffer:
                self._receive()
            head, self._buffer = self._buffer.split(b"\r\n\r\n", 1)
            status = head.split(b"\r\n", 1)[0].decode(errors="replace")
            if status.split(" ")[1:2] != ["101"]:
                raise _CdpError(status)
        except BaseException:
            self._sock.close()
            raise

    def __enter__(self) -> _CdpConnection:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._sock.close()

    def first_page(self) -> str:
        # The start page can register a moment after the endpoint listens.
        while True:
            targets = self.call("Target.getTargets")["targetInfos"]
            page = next((target["targetId"] for target in targets if target["type"] == "page"), None)
            if page is not None:
                return str(page)
            if time.monotonic() >= self._deadline:
                raise _CdpError(MSG_LOCAL_VIEWPORT_NO_PAGE)
            time.sleep(LOCAL_BROWSER_STARTUP_POLL)

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._last_id += 1
        self._send(json.dumps({"id": self._last_id, "method": method, "params": params or {}}).encode())
        while True:
            reply = json.loads(self._read_frame())
            if reply.get("id") != self._last_id:
                continue
            if "error" in reply:
                raise _CdpError(f"{method}: {reply['error'].get('message', reply['error'])}")
            result: dict[str, Any] = reply["result"]
            return result

    def _send(self, payload: bytes) -> None:
        # A client's frames must be masked; the browser drops the connection over one that is not.
        mask = os.urandom(4)
        size = len(payload)
        if size < 126:
            length = bytes([0x80 | size])
        elif size < 1 << 16:
            length = bytes([0x80 | 126]) + size.to_bytes(2, "big")
        else:
            length = bytes([0x80 | 127]) + size.to_bytes(8, "big")
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._sock.sendall(bytes([0x81]) + length + mask + masked)

    def _read_frame(self) -> bytes:
        while True:
            opcode, size = self._take(2)
            if size & 0x7F == 126:
                length = int.from_bytes(self._take(2), "big")
            elif size & 0x7F == 127:
                length = int.from_bytes(self._take(8), "big")
            else:
                length = size & 0x7F
            payload = self._take(length)
            if opcode & 0x0F == 0x8:
                raise _CdpError(MSG_LOCAL_CDP_CLOSED)
            if opcode & 0x0F == 0x1:
                return payload

    def _take(self, count: int) -> bytes:
        while len(self._buffer) < count:
            self._receive()
        chunk, self._buffer = self._buffer[:count], self._buffer[count:]
        return chunk

    def _receive(self) -> None:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        self._sock.settimeout(remaining)
        data = self._sock.recv(65536)
        if not data:
            raise _CdpError(MSG_LOCAL_CDP_CLOSED)
        self._buffer += data


def _read_port_file(profile: Path) -> tuple[int, str] | None:
    try:
        lines = (profile / LOCAL_DEVTOOLS_PORT_FILE).read_text().splitlines()
    except OSError:
        return None
    # The browser may be caught halfway through writing it; a later poll sees the whole file.
    if len(lines) < 2 or not lines[0].isdigit() or not lines[1].startswith("/"):
        return None
    return int(lines[0]), lines[1]


def _read_log(root: Path) -> tuple[int, str] | None:
    match = _LISTENING.search(_log_text(root))
    if match is None:
        return None
    url = urlsplit(match.group(1))
    return (url.port, url.path) if url.port is not None and url.path else None


def _log_text(root: Path) -> str:
    try:
        return (root / LOCAL_BROWSER_LOG_FILE).read_text(errors="replace")
    except OSError:
        return ""


def _end(launched: _Launched) -> OSError | None:
    """Stop the timer and the process, and remove the profile even if stopping fails.

    Raises if the process will not stop; returns why the profile could not be
    removed, or ``None`` once it is gone.
    """
    if launched.timer is not None:
        launched.timer.cancel()
    try:
        _stop(launched.process)
    finally:
        leftover = _remove_profile(launched.root)
    return leftover


def _remove_profile(root: Path) -> OSError | None:
    try:
        shutil.rmtree(root)
    except FileNotFoundError:
        # Already gone -- by an earlier attempt that got partway, or by hand.
        return None
    except OSError as exc:
        return exc
    return None


def _warn_if_left(root: Path, error: OSError | None) -> None:
    if error is not None:
        warnings.warn(MSG_LOCAL_PROFILE_LEFT.format(path=root, error=error), RuntimeWarning, stacklevel=2)


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=LOCAL_BROWSER_TERMINATE_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=LOCAL_BROWSER_KILL_TIMEOUT)


def _stop_all(sessions: dict[str, _Launched], lock: threading.Lock) -> None:
    with lock:
        survivors = list(sessions.values())
        sessions.clear()
    for launched in survivors:
        # Runs at interpreter exit, where there is nobody to raise to: one
        # browser that will not stop must not leave the others running.
        with contextlib.suppress(Exception):
            _end(launched)
