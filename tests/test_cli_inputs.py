"""What the ``webrouter`` command refuses before anything is sent, and what it keeps as given."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeBrowseProvider, FakeResearchProvider
from tests.test_cli import _run, _spy
from web_router.cli.args import pairs
from web_router.cli.output import to_json, write_error
from web_router.constants_cli import EXIT_FAILED, EXIT_OK, EXIT_USAGE
from web_router.types import ResearchResponse


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


def test_a_number_too_large_for_a_float_stays_text_or_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    assert pairs(["cap=1e400", 'deep={"a": [1e400]}', "ok=1e300"]) == {
        "cap": "1e400",
        "deep": '{"a": [1e400]}',
        "ok": 1e300,
    }
    status, out, err = _run(capsys, "research", "q", "--schema", '{"maximum": 1e400}')
    assert (status, out, err["error"]["type"]) == (EXIT_USAGE, None, "CliInputError")


def test_a_file_that_is_not_text_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    schema = tmp_path / "bad.json"
    schema.write_bytes(b"\xff\xfe{}")
    status, out, err = _run(capsys, "research", "q", "--schema", f"@{schema}")
    assert (status, out, err["error"]["type"]) == (EXIT_USAGE, None, "CliInputError")


def test_a_users_own_raw_key_is_kept_in_output() -> None:
    response = ResearchResponse(
        provider="fake",
        query="q",
        structured={"raw": 1, "rows": [{"raw": 2}]},
        raw=[{"backend": "payload"}],
    )
    printed = json.loads(to_json(response, raw=False, pretty=False))
    assert "raw" not in printed  # the model's own payload goes
    assert printed["structured"] == {"raw": 1, "rows": [{"raw": 2}]}  # the caller's data stays


@pytest.mark.parametrize(
    "argv",
    [
        ("search", "q", "--timeout", "abc"),
        ("live-data", "sources", "--limit", "x"),
        ("live-data", "sources", "--offset", "1.5"),
    ],
)
def test_a_flag_that_is_not_a_number_is_refused(argv: tuple[str, ...], capsys: pytest.CaptureFixture[str]) -> None:
    status, out, err = _run(capsys, *argv)
    assert (status, out, err["error"]["type"]) == (EXIT_USAGE, None, "UsageError")
    assert "invalid" in err["error"]["message"]


@pytest.mark.parametrize(
    "argv",
    [
        ("research", "q", "-p", "fake", "--schema", "null"),
        ("research", "q", "-p", "fake", "--records", "null"),
        ("live-data", "run", "NAME", "-p", "fake", "--inputs", "null"),
    ],
)
def test_an_explicit_null_is_refused_not_read_as_omitted(
    argv: tuple[str, ...],
    fake_research_provider: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    made = _spy(monkeypatch, FakeResearchProvider)
    status, out, err = _run(capsys, *argv)
    assert (status, out, err["error"]["type"]) == (EXIT_USAGE, None, "CliInputError")
    assert not any(instance.requests for instance in made)


def test_an_integer_too_long_to_read_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    status, out, err = _run(capsys, "research", "q", "--schema", '{"maximum": ' + "9" * 5000 + "}")
    assert (status, out, err["error"]["type"]) == (EXIT_USAGE, None, "CliInputError")


def test_a_stream_that_cannot_encode_text_gets_escaped_json() -> None:
    out, err = (io.TextIOWrapper(io.BytesIO(), encoding="ascii") for _ in range(2))
    response = ResearchResponse(provider="fake", query="café", answer="naïve")
    out.write(to_json(response, raw=False, pretty=False, stream=out))  # unescaped, this raises
    assert write_error(ValueError("déjà vu"), err) == EXIT_FAILED
    out.flush()
    err.flush()
    assert json.loads(out.buffer.getvalue())["answer"] == "naïve"  # type: ignore[attr-defined]
    assert json.loads(err.buffer.getvalue())["error"]["message"] == "déjà vu"  # type: ignore[attr-defined]


def test_search_can_leave_out_data_apis_or_keep_the_configured_ones(
    fake_provider: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATADOG_API_KEY", raising=False)
    monkeypatch.delenv("DD_API_KEY", raising=False)
    _, listed, _ = _run(capsys, "search", "datadog monitors", "-p", "fake")
    _, left_out, _ = _run(capsys, "search", "datadog monitors", "-p", "fake", "--no-data-apis")
    _, keyed, _ = _run(capsys, "search", "datadog monitors", "-p", "fake", "--configured-only")
    assert listed["data_apis"]
    assert left_out["data_apis"] == [] and keyed["data_apis"] == []


@pytest.mark.parametrize("extra", [("--depth", "deep"), ("-p", "fake"), ("--country", "US"), ("--no-data-apis",)])
def test_data_apis_only_refuses_what_it_cannot_honour(
    extra: tuple[str, ...], fake_provider: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    status, out, err = _run(capsys, "search", "datadog monitors", "--data-apis-only", *extra)
    assert (status, out, err["error"]["type"]) == (EXIT_USAGE, None, "CliInputError")
