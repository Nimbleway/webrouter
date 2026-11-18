"""``log_spend()`` and the spend log: one entry per answered call, and a report by day, backend and method."""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.spend_helpers import Kept
from web_router import AsyncWebRouter, Budget, InvalidParameterError, SpendEntry, SpendLog, WebRouter, spend_report
from web_router.exceptions import ConfigurationError


class _Broken:
    def record(self, entry: SpendEntry) -> None:
        raise OSError("disk full")


def _entry(day: int, provider: str, method: str, cost: float | None, source: str | None = "estimated") -> SpendEntry:
    at = datetime(2026, 11, day, 12, tzinfo=UTC)
    return SpendEntry(at=at, method=method, provider=provider, cost_usd=cost, cost_source=source, elapsed_s=1.0)  # type: ignore[arg-type]


def test_a_path_logs_every_answered_call_as_a_json_line(tmp_path: Path, fake_provider: Any) -> None:
    router = WebRouter("fake")
    log = router.log_spend(tmp_path / "logs" / "spend.jsonl")
    assert isinstance(log, SpendLog)
    router.search("a private question")
    router.search("a private question", budget=Budget(time_limit_s=60))
    lines = (tmp_path / "logs" / "spend.jsonl").read_text().splitlines()
    assert [json.loads(line)["method"] for line in lines] == ["search", "search"]
    entries = log.entries()
    assert [entry.provider for entry in entries] == ["fake", "fake"]
    assert all(entry.elapsed_s is not None for entry in entries)
    assert "private" not in (tmp_path / "logs" / "spend.jsonl").read_text(), "no query is logged"


async def test_the_async_router_logs_to_any_sink(fake_provider: Any) -> None:
    router = AsyncWebRouter("fake")
    sink = Kept()
    assert router.log_spend(sink) is sink
    await router.search("q")
    assert [(entry.method, entry.provider) for entry in sink.entries] == [("search", "fake")]


def test_calls_through_a_budget_scope_are_logged(fake_provider: Any) -> None:
    router = WebRouter("fake")
    sink = Kept()
    router.log_spend(sink)
    with router.budget(Budget(time_limit_s=60)) as b:
        b.search("q")
    assert len(sink.entries) == 1


def test_none_stops_logging(fake_provider: Any) -> None:
    router = WebRouter("fake")
    sink = Kept()
    router.log_spend(sink)
    assert router.log_spend(None) is None
    router.search("q")
    assert sink.entries == []


def test_a_sink_that_fails_is_warned_about_and_the_answer_returned(
    fake_provider: Any, caplog: pytest.LogCaptureFixture
) -> None:
    router = WebRouter("fake")
    router.log_spend(_Broken())
    with caplog.at_level(logging.WARNING, logger="web_router"):
        response = router.search("q")
    assert response.provider == "fake"
    assert "_Broken" in caplog.text and "OSError" in caplog.text


def test_anything_else_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="int"):
        WebRouter().log_spend(42)  # type: ignore[arg-type]


def test_a_report_sums_each_group_and_counts_the_unpriced() -> None:
    entries = [
        _entry(1, "exa", "answer", 0.005, "reported"),
        _entry(1, "exa", "answer", 0.005, "reported"),
        _entry(1, "tavily", "search", 0.008),
        _entry(2, "exa", "answer", None, None),
    ]
    rows = spend_report(entries)
    assert [(row.key, row.calls, row.cost_usd, row.unpriced_calls) for row in rows] == [
        ({"day": "2026-11-01", "provider": "exa", "method": "answer"}, 2, pytest.approx(0.01), 0),
        ({"day": "2026-11-01", "provider": "tavily", "method": "search"}, 1, pytest.approx(0.008), 0),
        ({"day": "2026-11-02", "provider": "exa", "method": "answer"}, 1, 0.0, 1),
    ]
    assert rows[0].reported_usd == pytest.approx(0.01)
    assert rows[1].estimated_usd == pytest.approx(0.008)
    by_source = spend_report(entries, by=["cost_source"])
    assert [(row.key["cost_source"], row.calls) for row in by_source] == [("", 1), ("estimated", 1), ("reported", 2)]


def test_a_report_by_an_unknown_key_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="query"):
        spend_report([], by=["query"])  # type: ignore[list-item]


def test_a_file_log_reads_back_from_a_day_on(tmp_path: Path) -> None:
    log = SpendLog(tmp_path / "spend.jsonl")
    assert log.entries() == []
    for entry in (_entry(1, "exa", "answer", 0.005), _entry(3, "exa", "answer", 0.005)):
        log.record(entry)
    assert [row.key["day"] for row in log.report(by=["day"], since=date(2026, 11, 2))] == ["2026-11-03"]


def test_a_line_left_unfinished_is_skipped_and_the_rest_read(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    log = SpendLog(tmp_path / "spend.jsonl")
    log.record(_entry(1, "fake", "search", 0.01))
    with (tmp_path / "spend.jsonl").open("a", encoding="utf-8") as file:
        file.write('{"at": "2026-11-02T12:00", "provider": "\u00e9')
    with (tmp_path / "spend.jsonl").open("ab") as raw:
        raw.write("é".encode()[:1])  # half a two-byte character, as an interrupted write leaves it
    log.record(_entry(3, "next", "search", 0.02))
    with caplog.at_level(logging.WARNING, logger="web_router"):
        assert [entry.provider for entry in log.entries()] == ["fake", "next"], "the next entry is not joined to it"
    assert "not a whole entry" in caplog.text


def test_a_naive_time_is_read_as_utc_on_any_host() -> None:
    naive = SpendEntry(at=datetime(2026, 11, 2, 23, 30), method="search", provider="fake")
    assert naive.at.tzinfo is UTC and naive.day == date(2026, 11, 2)


def test_a_reported_cost_never_fills_the_whole_call_estimate(fake_provider: Any) -> None:
    from web_router.spend_record import spend_entry
    from web_router.types import SearchResponse

    billed = SearchResponse(query="q", provider="fake", results=[], cost_usd=0.02, cost_source="reported")
    assert spend_entry(billed, "search").call_estimate_usd is None
    estimated = billed.model_copy(update={"cost_source": "estimated"})
    assert spend_entry(estimated, "search").call_estimate_usd == 0.02


@pytest.mark.parametrize("field", ["cost_usd", "call_estimate_usd", "elapsed_s"])
def test_a_non_finite_amount_is_refused(field: str) -> None:
    with pytest.raises(ValueError, match="finite"):
        SpendEntry(at=datetime(2026, 11, 2, tzinfo=UTC), method="search", provider="fake", **{field: float("nan")})
