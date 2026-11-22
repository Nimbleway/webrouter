"""Removing a local browser's temporary profile, and saying so when it cannot be."""

from __future__ import annotations

import errno
import shutil
import time
import warnings
from pathlib import Path

from web_router.constants import MSG_LOCAL_PROFILE_LEFT
from web_router.constants_local import LOCAL_PROFILE_REMOVE_DELAY, LOCAL_PROFILE_REMOVE_RETRIES


def remove_profile(root: Path) -> OSError | None:
    """Remove ``root``, and return why it could not be, or ``None`` once it is gone.

    A folder found not yet empty is tried again: a helper the browser started may still be writing to it.
    """
    for _ in range(LOCAL_PROFILE_REMOVE_RETRIES):
        error = _remove_once(root)
        if error is None or error.errno != errno.ENOTEMPTY:
            return error
        time.sleep(LOCAL_PROFILE_REMOVE_DELAY)
    return _remove_once(root)


def _remove_once(root: Path) -> OSError | None:
    try:
        shutil.rmtree(root)
    except FileNotFoundError:
        # Already gone -- by an earlier attempt that got partway, or by hand.
        return None
    except OSError as exc:
        return exc
    return None


def warn_if_left(root: Path, error: OSError | None) -> None:
    """Warn that ``root`` holds a stopped session's cookies and storage still, when ``error`` says it does."""
    if error is not None:
        warnings.warn(MSG_LOCAL_PROFILE_LEFT.format(path=root, error=error), RuntimeWarning, stacklevel=2)
