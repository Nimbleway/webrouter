"""What the ``webrouter`` command refuses before anything is sent, and what it keeps as given."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import FakeBrowseProvider, FakeResearchProvider
from tests.test_cli import _run, _spy
from web_router.constants_cli import EXIT_OK, EXIT_USAGE


@pytest.mark.parametrize(
    ("command", "backend", "cls"),
    [
        ("research", "fake_research_provider", FakeResearchProvider),
        ("browse", "fake_browse_provider", FakeBrowseProvider),
    ],
)
@pytest.mark.parametrize("wait", ["-1", "0", "nan", "inf"])
def test_a_wait_that_is_not_a_finite_positive_number_starts_nothing(
    command: str,
    backend: str,
    cls: type[Any],
    wait: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request.getfixturevalue(backend)
    made = _spy(monkeypatch, cls)
    status, out, err = _run(capsys, command, "x", "-p", "fake", "--timeout", wait)
    assert (status, out, err["error"]["type"]) == (EXIT_USAGE, None, "UsageError")
    assert not any(instance.requests for instance in made)
    assert _run(capsys, command, "x", "-p", "fake", "--timeout", "5")[0] == EXIT_OK
    assert any(instance.requests for instance in made)
