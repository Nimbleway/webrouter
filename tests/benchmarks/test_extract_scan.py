"""The extract scan's rows and report -- the tables it builds from measured rows, not the pages it fetches."""

from __future__ import annotations

from types import ModuleType

from tests.benchmarks.conftest import EXTRACT_SETUP, extract_row
from web_router import BenchmarkTimeoutError, ExtractBenchmarkRun, ProviderError
from web_router.types import ExtractedDocument, ExtractResponse


def test_a_row_records_what_came_back_and_nothing_it_did_not(extract_scan: ModuleType) -> None:
    document = ExtractedDocument(url="https://a.test", content="hello", format="markdown", status_code=200)
    answered = ExtractBenchmarkRun(
        provider="nimble", latency=0.5, response=ExtractResponse(provider="nimble", documents=[document])
    )
    row = extract_scan.row_of("https://a.test", "News / Media", answered)
    assert row["returned"] and row["with_text"] and row["chars"] == 5
    assert row["format"] == "markdown" and row["status_code"] == 200 and row["latency"] == 0.5
    assert row["error"] is None and row["error_type"] is None

    empty = ExtractedDocument(url="https://a.test", content="", format="markdown")
    row = extract_scan.row_of(
        "https://a.test",
        None,
        ExtractBenchmarkRun(provider="jina", latency=0.1, response=ExtractResponse(provider="jina", documents=[empty])),
    )
    assert row["returned"] and not row["with_text"] and row["chars"] == 0 and row["status_code"] is None

    blank = ExtractedDocument(url="https://a.test", content="\n\n  \t", format="markdown")
    row = extract_scan.row_of(
        "https://a.test",
        None,
        ExtractBenchmarkRun(provider="jina", latency=0.1, response=ExtractResponse(provider="jina", documents=[blank])),
    )
    assert row["returned"] and not row["with_text"] and row["chars"] == 0, "whitespace is not text"

    failed = ExtractBenchmarkRun(provider="exa", latency=2.0, error=BenchmarkTimeoutError("exa", 2.0))
    row = extract_scan.row_of("https://a.test", None, failed)
    assert not row["returned"] and row["chars"] == 0 and row["format"] is None
    assert row["error_type"] == "BenchmarkTimeoutError" and "exa" in row["error"]


def test_empty_failed_timed_out_refused_and_not_configured_are_counted_apart(extract_scan: ModuleType) -> None:
    rows = [extract_row("nimble", f"u{i}", 100) for i in range(7)] + [extract_row("nimble", "u7", 0)]
    rows += [extract_row("nimble", "u8", None), extract_row("nimble", "u9", None, error_type="BenchmarkTimeoutError")]
    rows += [extract_row("nimble", "u10", None, error_type="UnsupportedFormatError")]
    rows += [extract_row("nimble", "u11", None, error_type="ConfigurationError")]
    summary = extract_scan.summarize(rows, {**EXTRACT_SETUP, "providers": ["nimble"]})
    score = summary["overall"]["nimble"]

    assert score["urls"] == 12 and score["returned"] == 8 and score["with_text"] == 7
    assert score["with_text_rate"] == 7 / 12
    assert score["empty"] == 1 and score["failed"] == 1 and score["timed_out"] == 1 and score["refused"] == 1
    assert score["not_configured"] == 1, "a backend never built made no call: not a failed one"
    assert score["latency_p50"] == 1.0 and score["chars_median"] == 100
    assert "| Not configured |" in extract_scan.markdown(summary)


def test_a_backend_with_no_key_reads_as_not_configured_not_as_unreliable(extract_scan: ModuleType) -> None:
    """`--providers oxylabs` without its password used to report 100 failed calls, none of which was made."""
    from web_router.exceptions import ConfigurationError, ProviderNotInstalledError

    never_built = [
        ExtractBenchmarkRun(provider="oxylabs", latency=0.0, error=ConfigurationError("no OXYLABS_PASSWORD")),
        ExtractBenchmarkRun(provider="oxylabs", latency=0.0, error=ProviderNotInstalledError("oxylabs", "extract")),
    ]
    rows = [extract_scan.row_of(f"u{i}", None, run) for i, run in enumerate(never_built)]
    score = extract_scan.summarize(rows, {**EXTRACT_SETUP, "providers": ["oxylabs"]})["overall"]["oxylabs"]

    assert score["not_configured"] == 2 and score["failed"] == 0


def test_a_call_that_answered_with_no_document_is_empty_not_failed(extract_scan: ModuleType) -> None:
    """The public path never answers with no document, but a row that did is a call that answered, with nothing."""
    answered_with_nothing = ExtractBenchmarkRun(
        provider="odd", latency=0.3, response=ExtractResponse.model_construct(provider="odd", documents=[])
    )
    row = extract_scan.row_of("u0", None, answered_with_nothing)
    assert row["returned"] and not row["with_text"] and row["error"] is None

    score = extract_scan.summarize([row], {**EXTRACT_SETUP, "providers": ["odd"]})["overall"]["odd"]
    assert score["empty"] == 1 and score["failed"] == 0


def test_the_best_backend_per_category_is_named_on_pages_with_text_not_on_the_few_it_served(
    extract_scan: ModuleType,
) -> None:
    """A backend that failed most of a category cannot lead it by returning text on the rest."""
    rows = [extract_row("flaky", "u0", 100)] + [extract_row("flaky", f"u{i}", None) for i in range(1, 12)]
    rows += [extract_row("steady", f"u{i}", 100 if i < 10 else 0) for i in range(12)]
    summary = extract_scan.summarize(rows, {**EXTRACT_SETUP, "providers": ["flaky", "steady"]})

    group = summary["by_category"]["News / Media"]
    assert group["urls"] == 12
    assert group["with_text_rate"] == {"flaky": 1 / 12, "steady": 10 / 12}
    assert group["leaders"] == ["steady"]
    assert "**83%**" in extract_scan.markdown(summary)


def test_a_category_too_small_to_tell_backends_apart_names_no_best(extract_scan: ModuleType) -> None:
    rows = [extract_row("nimble", "u0", 100, category="pdf"), extract_row("jina", "u0", None, category="pdf")]
    summary = extract_scan.summarize(rows, {**EXTRACT_SETUP, "providers": ["nimble", "jina"]})

    assert summary["by_category"]["pdf"]["leaders"] == []


def test_a_url_with_no_category_is_grouped_as_uncategorized(extract_scan: ModuleType) -> None:
    rows = [extract_row("nimble", "u0", 100, category=None)]  # type: ignore[arg-type]
    summary = extract_scan.summarize(rows, {**EXTRACT_SETUP, "providers": ["nimble"]})

    assert list(summary["by_category"]) == ["uncategorized"]


def test_the_recommended_auto_order_ranks_on_pages_with_text_and_says_which_the_run_tells_apart(
    extract_scan: ModuleType,
) -> None:
    """A suggestion, opt-in: backends by pages with text over every URL, `auto` and a silent backend left out."""
    counts = {"best": 40, "close": 38, "far": 20, "auto": 40, "silent": 0}
    rows = []
    for provider, hits in counts.items():
        for i in range(40):
            rows.append(extract_row(provider, f"u{i}", 100 if i < hits else (None if provider == "silent" else 0)))
    summary = extract_scan.summarize(rows, {**EXTRACT_SETUP, "providers": list(counts)})

    assert extract_scan.recommended_order(summary) == [("best", None), ("close", False), ("far", True)]

    report = extract_scan.markdown(summary)
    section = report.split("## Recommended auto order")[1].split("\n## ")[0]
    assert "| 1 | `best` |" in section and "| best |" in section
    assert "| 2 | `close` |" in section and "not shown apart" in section
    assert "| 3 | `far` |" in section and "behind" in section
    assert "`auto`" not in section and "`silent`" not in section
    assert 'WebRouter(provider_config={"auto": {"chain": {"extract": ["best", "close", "far"]}}})' in section
    assert "opt-in" in section and "backed by a scan" in section
    assert report.index("## Overall") < report.index("## Recommended auto order") < report.index("## By category")


def test_a_scan_where_nothing_came_back_suggests_no_order(extract_scan: ModuleType) -> None:
    summary = extract_scan.summarize([extract_row("nimble", "u0", None)], {**EXTRACT_SETUP, "providers": ["nimble"]})

    assert extract_scan.recommended_order(summary) == []
    assert "no order to suggest" in extract_scan.markdown(summary)


def test_the_report_names_the_set_the_format_and_the_urls_fetched_not_the_limit(extract_scan: ModuleType) -> None:
    rows = [extract_row("nimble", f"u{i}", 100) for i in range(12)]
    summary = extract_scan.summarize(
        rows, {**EXTRACT_SETUP, "providers": ["nimble"], "limit": 100, "asked": 12, "format": "html"}
    )
    report = extract_scan.markdown(summary)

    assert report.splitlines()[0].startswith("# Extract backends on the general mix")
    assert report.splitlines()[2].startswith("12 URLs of the general mix")
    assert "`format=html`" in report
    assert "no format named" in extract_scan.markdown(
        extract_scan.summarize(rows, {**EXTRACT_SETUP, "providers": ["nimble"]})
    )


def test_a_repeated_provider_name_is_scanned_and_reported_once(extract_scan: ModuleType) -> None:
    assert extract_scan.explicit_providers("a,b,a") == ["a", "b"]
    assert extract_scan.explicit_providers("a, b ,,") == ["a", "b"]
    assert extract_scan.explicit_providers("a,b") == ["a", "b"]


def test_provider_names_are_spelled_as_the_rows_label_them(extract_scan: ModuleType) -> None:
    """`--providers Nimble,Jina` used to score every backend 0/0: the rows say `nimble`, the setup said `Nimble`."""
    assert extract_scan.explicit_providers("Nimble,JINA,nimble") == ["nimble", "jina"]

    rows = [
        extract_scan.row_of(
            "u0", None, ExtractBenchmarkRun(provider="nimble", latency=0.1, error=ProviderError("nimble", "x"))
        )
    ]
    summary = extract_scan.summarize(rows, {**EXTRACT_SETUP, "providers": extract_scan.explicit_providers("Nimble")})
    assert summary["overall"]["nimble"]["urls"] == 1
