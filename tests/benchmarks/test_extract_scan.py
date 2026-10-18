"""The extract scan's rows, report and merging of scans -- the tables it builds, not the pages it fetches."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from web_router import BenchmarkTimeoutError, ExtractBenchmarkRun
from web_router.types import ExtractedDocument, ExtractResponse

SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "providers" / "extract.py"


@pytest.fixture(scope="module")
def scan() -> Iterator[ModuleType]:
    # The scan imports what it shares with its neighbour as a sibling, as it does run from its folder.
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec = importlib.util.spec_from_file_location("extract_scan", SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(SCRIPT.parent))


def _row(
    provider: str,
    url: str,
    chars: int | None,
    category: str = "News / Media",
    error_type: str | None = "ProviderError",
) -> dict[str, Any]:
    """A measured row: ``chars`` is the text that came back, ``None`` a call that did not answer."""
    returned = chars is not None
    return {
        "url": url,
        "category": category,
        "provider": provider,
        "returned": returned,
        "with_text": bool(chars),
        "chars": chars or 0,
        "format": "markdown" if returned else None,
        "status_code": 200 if returned else None,
        "latency": 1.0,
        "error": None if returned else "HTTP 500",
        "error_type": None if returned else error_type,
    }


SETUP = {
    "urls": "general",
    "urls_title": "the general mix",
    "urls_sha256": "abc",
    "url_column": "url",
    "category_column": "category",
    "limit": 12,
    "seed": 0,
    "format": None,
    "timeout": 120.0,
    "started": "2026-10-13T00:00:00+00:00",
}


def test_a_row_records_what_came_back_and_nothing_it_did_not(scan: ModuleType) -> None:
    document = ExtractedDocument(url="https://a.test", content="hello", format="markdown", status_code=200)
    answered = ExtractBenchmarkRun(
        provider="nimble", latency=0.5, response=ExtractResponse(provider="nimble", documents=[document])
    )
    row = scan.row_of("https://a.test", "News / Media", answered)
    assert row["returned"] and row["with_text"] and row["chars"] == 5
    assert row["format"] == "markdown" and row["status_code"] == 200 and row["latency"] == 0.5
    assert row["error"] is None and row["error_type"] is None

    empty = ExtractedDocument(url="https://a.test", content="", format="markdown")
    row = scan.row_of(
        "https://a.test",
        None,
        ExtractBenchmarkRun(provider="jina", latency=0.1, response=ExtractResponse(provider="jina", documents=[empty])),
    )
    assert row["returned"] and not row["with_text"] and row["chars"] == 0 and row["status_code"] is None

    failed = ExtractBenchmarkRun(provider="exa", latency=2.0, error=BenchmarkTimeoutError("exa", 2.0))
    row = scan.row_of("https://a.test", None, failed)
    assert not row["returned"] and row["chars"] == 0 and row["format"] is None
    assert row["error_type"] == "BenchmarkTimeoutError" and "exa" in row["error"]


def test_empty_failed_timed_out_and_refused_are_counted_apart_and_only_text_counts_as_a_page(
    scan: ModuleType,
) -> None:
    rows = [_row("nimble", f"u{i}", 100) for i in range(8)] + [_row("nimble", "u8", 0)]
    rows += [_row("nimble", "u9", None), _row("nimble", "u10", None, error_type="BenchmarkTimeoutError")]
    rows += [_row("nimble", "u11", None, error_type="UnsupportedFormatError")]
    summary = scan.summarize(rows, {**SETUP, "providers": ["nimble"]})
    score = summary["overall"]["nimble"]

    assert score["urls"] == 12 and score["returned"] == 9 and score["with_text"] == 8
    assert score["with_text_rate"] == 8 / 12
    assert score["empty"] == 1 and score["failed"] == 1 and score["timed_out"] == 1 and score["refused"] == 1
    assert score["latency_p50"] == 1.0 and score["chars_median"] == 100


def test_the_best_backend_per_category_is_named_on_pages_with_text_not_on_the_few_it_served(
    scan: ModuleType,
) -> None:
    """A backend that failed most of a category cannot lead it by returning text on the rest."""
    rows = [_row("flaky", "u0", 100)] + [_row("flaky", f"u{i}", None) for i in range(1, 12)]
    rows += [_row("steady", f"u{i}", 100 if i < 10 else 0) for i in range(12)]
    summary = scan.summarize(rows, {**SETUP, "providers": ["flaky", "steady"]})

    group = summary["by_category"]["News / Media"]
    assert group["urls"] == 12
    assert group["with_text_rate"] == {"flaky": 1 / 12, "steady": 10 / 12}
    assert group["leaders"] == ["steady"]
    assert "**83%**" in scan.markdown(summary)


def test_a_category_too_small_to_tell_backends_apart_names_no_best(scan: ModuleType) -> None:
    rows = [_row("nimble", "u0", 100, category="pdf"), _row("jina", "u0", None, category="pdf")]
    summary = scan.summarize(rows, {**SETUP, "providers": ["nimble", "jina"]})

    assert summary["by_category"]["pdf"]["leaders"] == []


def test_a_url_with_no_category_is_grouped_as_uncategorized(scan: ModuleType) -> None:
    rows = [_row("nimble", "u0", 100, category=None)]  # type: ignore[arg-type]
    summary = scan.summarize(rows, {**SETUP, "providers": ["nimble"]})

    assert list(summary["by_category"]) == ["uncategorized"]


def test_the_recommended_auto_order_ranks_on_pages_with_text_and_says_which_the_run_tells_apart(
    scan: ModuleType,
) -> None:
    """A suggestion, opt-in: backends by pages with text over every URL, `auto` and a silent backend left out."""
    counts = {"best": 40, "close": 38, "far": 20, "auto": 40, "silent": 0}
    rows = []
    for provider, hits in counts.items():
        for i in range(40):
            rows.append(_row(provider, f"u{i}", 100 if i < hits else (None if provider == "silent" else 0)))
    summary = scan.summarize(rows, {**SETUP, "providers": list(counts)})

    assert scan.recommended_order(summary) == [("best", None), ("close", False), ("far", True)]

    report = scan.markdown(summary)
    section = report.split("## Recommended auto order")[1].split("\n## ")[0]
    assert "| 1 | `best` |" in section and "| best |" in section
    assert "| 2 | `close` |" in section and "not shown apart" in section
    assert "| 3 | `far` |" in section and "behind" in section
    assert "`auto`" not in section and "`silent`" not in section
    assert 'WebRouter(provider_config={"auto": {"chain": {"extract": ["best", "close", "far"]}}})' in section
    assert "opt-in" in section and "backed by a scan" in section
    assert report.index("## Overall") < report.index("## Recommended auto order") < report.index("## By category")


def test_a_scan_where_nothing_came_back_suggests_no_order(scan: ModuleType) -> None:
    summary = scan.summarize([_row("nimble", "u0", None)], {**SETUP, "providers": ["nimble"]})

    assert scan.recommended_order(summary) == []
    assert "no order to suggest" in scan.markdown(summary)


def test_the_report_names_the_set_the_format_and_the_urls_fetched_not_the_limit(scan: ModuleType) -> None:
    rows = [_row("nimble", f"u{i}", 100) for i in range(12)]
    summary = scan.summarize(rows, {**SETUP, "providers": ["nimble"], "limit": 100, "asked": 12, "format": "html"})
    report = scan.markdown(summary)

    assert report.splitlines()[0].startswith("# Extract backends on the general mix")
    assert report.splitlines()[2].startswith("12 URLs of the general mix")
    assert "`format=html`" in report
    assert "no format named" in scan.markdown(scan.summarize(rows, {**SETUP, "providers": ["nimble"]}))


def _scan(folder: Path, providers: list[str], **setup: Any) -> Path:
    folder.mkdir()
    (folder / "run.json").write_text(json.dumps({**SETUP, "providers": providers, **setup}))
    (folder / "rows.jsonl").write_text("".join(json.dumps(_row(p, "u0", 100)) + "\n" for p in providers))
    return folder


def test_scans_of_the_same_urls_combine_and_others_are_refused(scan: ModuleType, tmp_path: Path) -> None:
    first = _scan(tmp_path / "a", ["nimble", "jina"])
    later = _scan(tmp_path / "b", ["exa"])
    rows, setup = scan.combine([first, later])

    assert setup["providers"] == ["nimble", "jina", "exa"] and len(rows) == 3

    other_format = _scan(tmp_path / "c", ["tavily"], format="html")
    with pytest.raises(SystemExit, match="format"):
        scan.combine([first, other_format])
    other_file = _scan(tmp_path / "d", ["tavily"], urls_sha256="def")
    with pytest.raises(SystemExit, match="urls_sha256"):
        scan.combine([first, other_file])
    again = _scan(tmp_path / "e", ["nimble"])
    with pytest.raises(SystemExit, match="nimble again"):
        scan.combine([first, again])

    other_urls = tmp_path / "f"
    other_urls.mkdir()
    (other_urls / "run.json").write_text(json.dumps({**SETUP, "providers": ["tavily"]}))
    (other_urls / "rows.jsonl").write_text(json.dumps(_row("tavily", "elsewhere", 100)) + "\n")
    with pytest.raises(SystemExit, match="different URLs"):
        scan.combine([first, other_urls])


def test_the_shipped_sets_load_deduplicated_and_sampled_by_seed(scan: ModuleType) -> None:
    whole = scan.load_urls(scan.parse_args(["--limit", "0"]))
    assert len(whole) == 455 and len({url for url, _ in whole}) == 455
    assert all(category for _, category in whole)

    some = scan.load_urls(scan.parse_args(["--limit", "20", "--seed", "1"]))
    assert len(some) == 20 and some == scan.load_urls(scan.parse_args(["--limit", "20", "--seed", "1"]))
    assert some != scan.load_urls(scan.parse_args(["--limit", "20", "--seed", "2"]))
    assert [url for url, _ in some] == [url for url, _ in whole if url in {u for u, _ in some}], "set order kept"

    social = scan.load_urls(scan.parse_args(["--urls", "social-pdf", "--limit", "0"]))
    assert len(social) == 911 and {category for _, category in social} == {"pdf", "social", "other"}


def test_no_shipped_url_carries_a_credential_shaped_query_key(scan: ModuleType) -> None:
    """A token in a URL would be sent to every backend and written into every result file."""
    secret_keys = re.compile(r"[?&](token|ctoken|access_token|api_key|apikey|sig|signature|offertoken)=", re.IGNORECASE)
    for name in scan.URL_SETS:
        urls = scan.load_urls(scan.parse_args(["--urls", name, "--limit", "0"]))
        leaking = [url for url, _ in urls if secret_keys.search(url)]
        assert not leaking, f"{name}: {leaking[:3]}"


def test_concurrency_below_one_is_a_usage_error_not_a_hang(
    scan: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    for bad in ("0", "-1"):
        with pytest.raises(SystemExit) as raised:
            scan.parse_args(["--concurrency", bad])
        assert raised.value.code == 2 and "at least 1" in capsys.readouterr().err
    assert scan.parse_args(["--concurrency", "1"]).concurrency == 1


def test_a_repeated_provider_name_is_scanned_and_reported_once(scan: ModuleType) -> None:
    assert scan.explicit_providers("a,b,a") == ["a", "b"]
    assert scan.explicit_providers("a, b ,,") == ["a", "b"]
    assert scan.explicit_providers("a,b") == ["a", "b"]


def test_a_shipped_set_and_a_file_of_your_own_each_carry_their_identity(scan: ModuleType, tmp_path: Path) -> None:
    shipped = scan.urls_identity(scan.parse_args([]))
    assert shipped["urls"] == "general" and shipped["urls_title"] == "the general mix"
    assert len(shipped["urls_sha256"]) == 64 and shipped["url_column"] == "url"

    path = tmp_path / "mine.csv"
    path.write_text("link,kind\nhttps://a.test,blog\nhttps://a.test,blog\nhttps://b.test,\n", encoding="utf-8")
    args = scan.parse_args(["--urls", str(path), "--url-column", "link", "--category-column", "kind"])
    assert scan.load_urls(args) == [("https://a.test", "blog"), ("https://b.test", None)]
    mine = scan.urls_identity(args)
    assert (
        mine["urls"] == "csv" and mine["urls_title"] == "`mine.csv`" and mine["urls_sha256"] != shipped["urls_sha256"]
    )

    with pytest.raises(SystemExit, match="--url-column"):
        scan.load_urls(scan.parse_args(["--urls", str(path)]))
    with pytest.raises(SystemExit, match="not a shipped set"):
        scan.load_urls(scan.parse_args(["--urls", "nowhere"]))


def test_p95_is_the_nearest_rank_the_search_scan_uses(scan: ModuleType) -> None:
    assert scan._pct([float(i) for i in range(1, 13)], 0.95) == 12.0
