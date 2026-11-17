"""The ``web-router`` command line: JSON on stdout, a JSON error on stderr, an exit status to branch on."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from web_router.cli import run
from web_router.cli.args import CliInputError, pairs
from web_router.cli.output import exit_code
from web_router.constants_cli import EXIT_CONFIG, EXIT_FAILED, EXIT_OK, EXIT_USAGE
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.exceptions_provider import NoAnswerProviderError


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, Any, Any]:
    """Run one command line; its status, and each stream parsed as JSON (``None`` when empty)."""
    status = run(list(argv))
    out, err = capsys.readouterr()
    return status, json.loads(out) if out else None, json.loads(err) if err else None


def test_search_prints_the_response_as_json(fake_provider: Any, capsys: pytest.CaptureFixture[str]) -> None:
    status, out, err = _run(capsys, "search", "hello", "-p", "fake", "-n", "3")
    assert (status, err) == (EXIT_OK, None)
    assert out["provider"] == "fake"
    assert out["results"][0]["url"] == "https://example.com"


def test_raw_payload_only_when_asked(fake_extract_provider: Any, capsys: pytest.CaptureFixture[str]) -> None:
    _, plain, _ = _run(capsys, "extract", "https://a.example", "https://b.example", "-p", "fake")
    _, with_raw, _ = _run(capsys, "--raw", "extract", "https://a.example", "-p", "fake")
    assert [doc["url"] for doc in plain["documents"]] == ["https://a.example", "https://b.example"]
    assert "raw" not in plain
    assert with_raw["raw"] == [{"urls": ["https://a.example"]}]


@pytest.mark.parametrize("argv", [("--pretty", "providers"), ("providers", "--pretty")])
def test_output_flags_work_before_or_after_the_command(
    argv: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(list(argv)) == EXIT_OK
    assert capsys.readouterr().out.startswith("{\n")


def test_research_schema_from_a_file_asks_for_a_structured_answer(
    fake_research_provider: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    schema = tmp_path / "schema.json"
    schema.write_text('{"type": "object"}', encoding="utf-8")
    status, out, _ = _run(capsys, "research", "q", "-p", "fake", "--schema", f"@{schema}", "--timeout", "5")
    assert status == EXIT_OK
    assert out["structured"] == {"ok": True}


def test_usage_error_is_json_on_stderr_with_stdout_empty(capsys: pytest.CaptureFixture[str]) -> None:
    status, out, err = _run(capsys, "search")
    assert (status, out) == (EXIT_USAGE, None)
    assert err["error"]["type"] == "UsageError"
    assert "query" in err["error"]["message"]


@pytest.mark.parametrize(
    "argv",
    [
        ("search", "q", "-p", "no-such-backend"),
        ("search", "q", "--option", "no-equals-sign"),
        ("research", "q", "--schema", "{not json"),
        ("research", "q", "--schema", "[1]"),
        ("research", "q", "--schema", '{"maximum": Infinity}'),
        ("search", "q", "-n", "0"),
        ("search", "q", "--timeout", "inf"),
    ],
)
def test_refused_input_exits_with_usage(argv: tuple[str, ...], capsys: pytest.CaptureFixture[str]) -> None:
    status, out, err = _run(capsys, *argv)
    assert (status, out) == (EXIT_USAGE, None)
    assert err["error"]["message"]


def test_providers_lists_each_method(capsys: pytest.CaptureFixture[str]) -> None:
    status, out, _ = _run(capsys, "providers")
    assert status == EXIT_OK
    assert {"search", "extract", "answer", "research", "browse", "browser", "live_data"} == set(out)
    assert "auto" in out["search"]


@pytest.mark.parametrize(
    ("argv", "backend"),
    [
        (("search", "q"), "fake_provider"),
        (("answer", "q"), "fake_answer_provider"),
        (("research", "q"), "fake_research_provider"),
        (("browse", "a task", "--start-url", "https://example.com"), "fake_browse_provider"),
    ],
)
def test_max_cost_refuses_before_any_call(
    argv: tuple[str, ...], backend: str, request: pytest.FixtureRequest, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fake backend has no price, and a cost cap refuses a call it cannot price, before anything is sent."""
    request.getfixturevalue(backend)
    status, out, err = _run(capsys, *argv, "-p", "fake", "--max-cost", "1")
    assert (status, out) == (EXIT_USAGE, None)
    assert err["error"]["type"] == "BudgetExceededError"


def test_pairs_keep_text_when_untyped_and_for_non_finite_numbers() -> None:
    assert pairs(["id=12345"], typed=False) == {"id": "12345"}
    assert pairs(["cap=Infinity"]) == {"cap": "Infinity"}


def test_pairs_read_json_values_and_keep_text() -> None:
    assert pairs(["n=3", "mode=fast", 'flags={"a": true}']) == {"n": 3, "mode": "fast", "flags": {"a": True}}
    assert pairs(None) is None
    with pytest.raises(CliInputError):
        pairs(["=value"])


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ConfigurationError("no key"), EXIT_CONFIG),
        (NoAnswerProviderError("auto", ["exa", "you"], [], ["EXA_API_KEY"]), EXIT_CONFIG),
        (NoAnswerProviderError("auto", ["exa", "you"], ["exa", "you"], []), EXIT_USAGE),
        (ProviderError("fake", "backend down"), EXIT_FAILED),
        (RuntimeError("anything else"), EXIT_FAILED),
    ],
)
def test_exit_status_says_what_kind_of_failure(error: BaseException, code: int) -> None:
    assert exit_code(error) == code
