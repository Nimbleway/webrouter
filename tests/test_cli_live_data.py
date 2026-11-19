"""``webrouter live-data``: what it sends the router, and what it prints back."""

from __future__ import annotations

import json
from typing import Any

import pytest

from web_router.cli import run
from web_router.client import WebRouter
from web_router.constants_cli import EXIT_OK, EXIT_USAGE
from web_router.types_live_data_source import LiveDataSource, LiveDataSourcePage


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
    """Each live data method the command calls, with its arguments; none of them reaches a backend."""
    seen: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
    page = LiveDataSourcePage(
        provider="fake",
        sources=[LiveDataSource(provider="fake", name="x", raw={"big": 1})],
        raw={"top": 1},
    )

    def record(method: str, result: Any) -> Any:
        def call(_self: WebRouter, *args: Any, **kwargs: Any) -> Any:
            seen.append((method, args, kwargs))
            return result

        return call

    monkeypatch.setattr(WebRouter, "live_data_sources", record("sources", page))
    monkeypatch.setattr(WebRouter, "live_data_source", record("source", page.sources[0]))
    monkeypatch.setattr(WebRouter, "live_data", record("run", {"records": []}))
    return seen


def _out(capsys: pytest.CaptureFixture[str], *argv: str) -> Any:
    assert run(list(argv)) == EXIT_OK
    return json.loads(capsys.readouterr().out)


def test_sources_leave_out_each_sources_raw_too(calls: list[Any], capsys: pytest.CaptureFixture[str]) -> None:
    plain = _out(capsys, "live-data", "sources")
    with_raw = _out(capsys, "--raw", "live-data", "sources")
    assert "raw" not in plain and "raw" not in plain["sources"][0]
    assert with_raw["raw"] == {"top": 1} and with_raw["sources"][0]["raw"] == {"big": 1}


@pytest.mark.parametrize("flag", ["--limit", "--offset"])
def test_a_negative_page_is_refused_before_any_call(
    flag: str, calls: list[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(["live-data", "sources", "-p", "fake", flag, "-1"]) == EXIT_USAGE
    out, err = capsys.readouterr()
    assert out == "" and json.loads(err)["error"]["type"] == "UsageError"
    assert calls == []
    _out(capsys, "live-data", "sources", "-p", "fake", flag, "0")
    assert calls[0][2]["limit" if flag == "--limit" else "offset"] == 0


def test_run_inputs_stay_text_and_merge_with_inputs_json(calls: list[Any], capsys: pytest.CaptureFixture[str]) -> None:
    _out(capsys, "live-data", "run", "NAME", "-p", "fake", "--input", "id=12345", "--inputs", '{"x": 1}')
    method, args, kwargs = calls[0]
    assert (method, args) == ("run", ("NAME", {"x": 1, "id": "12345"}))
    assert kwargs["provider"] == "fake"


def test_sources_and_source_pass_their_arguments(calls: list[Any], capsys: pytest.CaptureFixture[str]) -> None:
    _out(capsys, "live-data", "sources", "-p", "fake", "-q", "amazon", "--limit", "5", "--offset", "10")
    _out(capsys, "live-data", "source", "NAME", "-p", "fake")
    assert calls == [
        ("sources", (), {"provider": "fake", "query": "amazon", "limit": 5, "offset": 10}),
        ("source", ("NAME",), {"provider": "fake"}),
    ]


@pytest.mark.parametrize(
    "argv",
    [("--pretty", "live-data", "sources"), ("live-data", "--pretty", "sources"), ("live-data", "sources", "--pretty")],
)
def test_pretty_works_at_every_level(
    argv: tuple[str, ...], calls: list[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(list(argv)) == EXIT_OK
    assert capsys.readouterr().out.startswith("{\n")


def test_a_flag_is_never_read_from_its_abbreviation(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["providers", "--pre"]) == EXIT_USAGE
    assert capsys.readouterr().out == ""
