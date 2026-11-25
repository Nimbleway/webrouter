"""Removing a local browser's profile while a helper process the browser started is still writing to it."""

from __future__ import annotations

import errno
from pathlib import Path
from typing import Any

import pytest

from web_router.constants_local import LOCAL_PROFILE_REMOVE_RETRIES
from web_router.providers import _local_profile


def _rmtree_not_empty(monkeypatch: pytest.MonkeyPatch, times: int) -> list[Path]:
    """Make removal find the folder not yet empty ``times`` times, as a late-writing helper would, then work."""
    attempts: list[Path] = []
    real = _local_profile.shutil.rmtree

    def rmtree(path: Any) -> None:
        attempts.append(Path(path))
        if len(attempts) <= times:
            raise OSError(errno.ENOTEMPTY, "Directory not empty", str(path))
        real(path)

    monkeypatch.setattr(_local_profile.shutil, "rmtree", rmtree)
    monkeypatch.setattr(_local_profile.time, "sleep", lambda _seconds: None)
    return attempts


def test_a_profile_still_being_written_is_removed_on_a_later_try(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "profile"
    (root / "Default").mkdir(parents=True)
    attempts = _rmtree_not_empty(monkeypatch, times=2)

    assert _local_profile.remove_profile(root) is None
    assert not root.exists()
    assert len(attempts) == 3


def test_a_profile_that_never_empties_is_reported_after_the_last_try(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "profile"
    root.mkdir()
    attempts = _rmtree_not_empty(monkeypatch, times=LOCAL_PROFILE_REMOVE_RETRIES + 1)

    error = _local_profile.remove_profile(root)

    assert error is not None and error.errno == errno.ENOTEMPTY
    assert len(attempts) == LOCAL_PROFILE_REMOVE_RETRIES + 1


def test_any_other_failure_is_reported_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[Path] = []

    def rmtree(path: Any) -> None:
        attempts.append(Path(path))
        raise PermissionError(errno.EACCES, "Permission denied", str(path))

    monkeypatch.setattr(_local_profile.shutil, "rmtree", rmtree)

    assert isinstance(_local_profile.remove_profile(tmp_path), PermissionError)
    assert attempts == [tmp_path]
