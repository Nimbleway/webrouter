"""The extract scan's files: the URL sets it loads, the folder it writes as it runs, and the scans it merges."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.benchmarks.conftest import EXTRACT_SETUP, extract_row


def _scan(folder: Path, providers: list[str], **setup: Any) -> Path:
    folder.mkdir()
    (folder / "run.json").write_text(json.dumps({**EXTRACT_SETUP, "providers": providers, **setup}))
    (folder / "rows.jsonl").write_text("".join(json.dumps(extract_row(p, "u0", 100)) + "\n" for p in providers))
    return folder


def test_scans_of_the_same_urls_combine_and_others_are_refused(extract_scan: ModuleType, tmp_path: Path) -> None:
    first = _scan(tmp_path / "a", ["nimble", "jina"])
    later = _scan(tmp_path / "b", ["exa"])
    rows, setup = extract_scan.combine([first, later])

    assert setup["providers"] == ["nimble", "jina", "exa"] and len(rows) == 3

    other_format = _scan(tmp_path / "c", ["tavily"], format="html")
    with pytest.raises(SystemExit, match="format"):
        extract_scan.combine([first, other_format])
    other_file = _scan(tmp_path / "d", ["tavily"], urls_sha256="def")
    with pytest.raises(SystemExit, match="urls_sha256"):
        extract_scan.combine([first, other_file])
    again = _scan(tmp_path / "e", ["nimble"])
    with pytest.raises(SystemExit, match="nimble again"):
        extract_scan.combine([first, again])

    other_urls = tmp_path / "f"
    other_urls.mkdir()
    (other_urls / "run.json").write_text(json.dumps({**EXTRACT_SETUP, "providers": ["tavily"]}))
    (other_urls / "rows.jsonl").write_text(json.dumps(extract_row("tavily", "elsewhere", 100)) + "\n")
    with pytest.raises(SystemExit, match="different URLs"):
        extract_scan.combine([first, other_urls])


def test_the_shipped_sets_load_deduplicated_and_sampled_by_seed(extract_scan: ModuleType) -> None:
    whole = extract_scan.load_urls(extract_scan.parse_args(["--limit", "0"]))
    assert whole and len({url for url, _ in whole}) == len(whole)
    assert all(category for _, category in whole)

    some = extract_scan.load_urls(extract_scan.parse_args(["--limit", "20", "--seed", "1"]))
    assert len(some) == 20 and some == extract_scan.load_urls(extract_scan.parse_args(["--limit", "20", "--seed", "1"]))
    assert some != extract_scan.load_urls(extract_scan.parse_args(["--limit", "20", "--seed", "2"]))
    assert [url for url, _ in some] == [url for url, _ in whole if url in {u for u, _ in some}], "set order kept"

    social = extract_scan.load_urls(extract_scan.parse_args(["--urls", "social-pdf", "--limit", "0"]))
    assert social and len({url for url, _ in social}) == len(social)
    assert {category for _, category in social} == {"pdf", "social", "other"}


def test_no_shipped_url_carries_a_credential_shaped_query_key(extract_scan: ModuleType) -> None:
    """A token in a URL would be sent to every backend and written into every result file."""
    secret_keys = re.compile(r"[?&](token|ctoken|access_token|api_key|apikey|sig|signature|offertoken)=", re.IGNORECASE)
    for name in extract_scan.URL_SETS:
        urls = extract_scan.load_urls(extract_scan.parse_args(["--urls", name, "--limit", "0"]))
        leaking = [url for url, _ in urls if secret_keys.search(url)]
        assert not leaking, f"{name}: {leaking[:3]}"


def test_concurrency_below_one_is_a_usage_error_not_a_hang(
    extract_scan: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    for bad in ("0", "-1"):
        with pytest.raises(SystemExit) as raised:
            extract_scan.parse_args(["--concurrency", bad])
        assert raised.value.code == 2 and "at least 1" in capsys.readouterr().err
    assert extract_scan.parse_args(["--concurrency", "1"]).concurrency == 1


def test_a_shipped_set_and_a_file_of_your_own_each_carry_their_identity(
    extract_scan: ModuleType, tmp_path: Path
) -> None:
    shipped = extract_scan.urls_identity(extract_scan.parse_args([]))
    assert shipped["urls"] == "general" and shipped["urls_title"] == "the general mix"
    assert len(shipped["urls_sha256"]) == 64 and shipped["url_column"] == "url"

    path = tmp_path / "mine.csv"
    path.write_text("link,kind\nhttps://a.test,blog\nhttps://a.test,blog\nhttps://b.test,\n", encoding="utf-8")
    args = extract_scan.parse_args(["--urls", str(path), "--url-column", "link", "--category-column", "kind"])
    assert extract_scan.load_urls(args) == [("https://a.test", "blog"), ("https://b.test", None)]
    mine = extract_scan.urls_identity(args)
    assert (
        mine["urls"] == "csv" and mine["urls_title"] == "`mine.csv`" and mine["urls_sha256"] != shipped["urls_sha256"]
    )

    with pytest.raises(SystemExit, match="--url-column"):
        extract_scan.load_urls(extract_scan.parse_args(["--urls", str(path)]))
    with pytest.raises(SystemExit, match="not a shipped set"):
        extract_scan.load_urls(extract_scan.parse_args(["--urls", "nowhere"]))


def test_a_scan_that_dies_leaves_run_json_and_the_rows_it_paid_for(
    extract_scan: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing waits for the end: the setup is written first and each URL's rows the moment they exist."""
    out = tmp_path / "scan"

    async def dying_run(args: Any, setup: dict[str, Any], sink: Any) -> list[dict[str, Any]]:
        setup["asked"] = 3
        sink([extract_row("nimble", "u0", 100)])
        sink([extract_row("nimble", "u1", None)])
        raise RuntimeError("connection dropped")

    with pytest.raises(RuntimeError, match="connection dropped"):
        extract_scan.scan_main(
            ["--out", str(out), "--providers", "nimble"],
            parse_args=extract_scan.parse_args,
            setup_of=extract_scan.setup_of,
            run=dying_run,
            combine=extract_scan.combine,
            summarize=extract_scan.summarize,
            markdown=extract_scan.markdown,
        )

    setup = json.loads((out / "run.json").read_text())
    assert setup["providers"] == ["nimble"], "run.json was written before the first call"
    assert setup["asked"] == 3, "what run added to the setup is on disk with the first rows"
    lines = [json.loads(line) for line in (out / "rows.jsonl").read_text().splitlines()]
    assert [(r["url"], r["returned"]) for r in lines] == [("u0", True), ("u1", False)]
    assert not (out / "report.md").exists(), "the report needs every row; the rows do not need the report"

    # --combine on the folder alone reports what it fetched, as a share of the URLs asked.
    report = extract_scan.markdown(extract_scan.summarize(*extract_scan.combine([out])))
    assert "**2 of the 3 URLs asked.**" in report


def test_a_finished_scan_writes_every_file_once_from_the_rows_it_ran(extract_scan: ModuleType, tmp_path: Path) -> None:
    out = tmp_path / "scan"

    async def quick_run(args: Any, setup: dict[str, Any], sink: Any) -> list[dict[str, Any]]:
        rows = [extract_row("nimble", "u0", 100)]
        sink(rows)
        setup["asked"] = 1
        return rows

    extract_scan.scan_main(
        ["--out", str(out), "--providers", "nimble"],
        parse_args=extract_scan.parse_args,
        setup_of=extract_scan.setup_of,
        run=quick_run,
        combine=extract_scan.combine,
        summarize=extract_scan.summarize,
        markdown=extract_scan.markdown,
    )

    assert json.loads((out / "run.json").read_text())["asked"] == 1, "run.json is written again with what run added"
    assert len((out / "rows.jsonl").read_text().splitlines()) == 1, "the sink's rows are not written twice"
    assert (out / "report.md").exists() and (out / "summary.json").exists()


def test_the_real_run_hands_each_urls_rows_to_the_sink(extract_scan: ModuleType, tmp_path: Path) -> None:
    """The scan itself, not a stand-in: on a registered backend, each URL's rows reach the sink as its own batch."""
    from tests.conftest import FakeExtractProvider
    from web_router.registry import register_extract_provider, unregister_extract_provider

    class Scanned(FakeExtractProvider):  # type: ignore[misc, valid-type]
        name = "scanned"

    path = tmp_path / "two.csv"
    path.write_text("url,category\nhttps://a.test,blog\nhttps://b.test,news\n", encoding="utf-8")
    args = extract_scan.parse_args(["--urls", str(path), "--providers", "scanned", "--limit", "0"])
    setup = extract_scan.setup_of(args)
    saved: list[list[dict[str, Any]]] = []
    register_extract_provider("scanned", Scanned)
    try:
        rows = asyncio.run(extract_scan.run(args, setup, saved.append))
    finally:
        unregister_extract_provider("scanned")

    batches = sorted([(r["url"], r["category"], r["provider"], r["returned"]) for r in batch] for batch in saved)
    assert batches == [[("https://a.test", "blog", "scanned", True)], [("https://b.test", "news", "scanned", True)]]
    by_url = lambda r: r["url"]  # noqa: E731
    assert sorted(rows, key=by_url) == sorted((r for batch in saved for r in batch), key=by_url)
    assert setup["asked"] == 2
