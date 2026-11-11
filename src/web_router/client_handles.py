"""The browser sessions a router hands out, tracked so ``close()`` can release them; apart so ``client.py`` is short."""

from __future__ import annotations

import threading
from contextlib import suppress
from typing import Generic, TypeVar

from web_router.providers.base import AsyncBrowserHandle, BrowserHandle

_HandleT = TypeVar("_HandleT", BrowserHandle, AsyncBrowserHandle)


class _HandleTracking(Generic[_HandleT]):
    """The open-session list both routers keep: set up in the router's ``__init__``, read by ``close()``."""

    _browser_handles: list[_HandleT]
    _handles_lock: threading.Lock

    def _track(self, handle: _HandleT) -> _HandleT:
        # Pruned on every open, so a long-lived router that opens thousands of
        # sessions holds only the ones still open. In place and under the lock:
        # rebuilding the list and reassigning it lost a handle to a concurrent
        # open that appended between the two.
        with self._handles_lock:
            self._browser_handles[:] = [held for held in self._browser_handles if not held.released]
            self._browser_handles.append(handle)
        return handle

    def _open_handles(self) -> list[_HandleT]:
        # Left tracked: a handle leaves only once its release succeeds, so one
        # that fails is still here for the next close() to retry.
        with self._handles_lock:
            self._browser_handles[:] = [held for held in self._browser_handles if not held.released]
            return list(self._browser_handles)

    def _untrack(self, handle: _HandleT) -> None:
        with self._handles_lock, suppress(ValueError):
            self._browser_handles.remove(handle)
