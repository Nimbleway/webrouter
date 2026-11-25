"""``obscura`` extract against a fake binary: the command it builds, and what it makes of the output."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tests.providers._obscura_fake import calls, make_fake
from web_router import AsyncWebRouter, WebRouter
from web_router.constants import ENV_OBSCURA_PATH, PROVIDER_OBSCURA
from web_router.exceptions import ConfigurationError, DeadlineExceededError, ProviderError, UnsupportedParameterError
from web_router.mcp.env_credentials import credential_variables
from web_router.providers import obscura
from web_router.providers.auto import AutoExtractProvider
from web_router.providers.obscura import find_obscura, obscura_installed
from web_router.registry import configured_extract_providers


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv(ENV_OBSCURA_PATH, raising=False)
    monkeypatch.setenv("FAKE_OBSCURA", "page")
    return make_fake(tmp_path)


def _router(fake: Path) -> WebRouter:
    return WebRouter(PROVIDER_OBSCURA, provider_config={PROVIDER_OBSCURA: {"executable_path": str(fake)}})


@pytest.mark.parametrize(
    ("asked", "dump"), [(None, "markdown"), ("markdown", "markdown"), ("html", "html"), ("text", "text")]
)
def test_the_format_becomes_the_dump_and_the_url_follows_a_separator(fake: Path, asked: str | None, dump: str) -> None:
    with _router(fake) as router:
        response = router.extract("https://example.com/a", format=asked)  # type: ignore[arg-type]
    document = response.documents[0]

    argv = calls(fake)[0]["argv"]
    assert argv == ["fetch", "--dump", dump, "--quiet", "--timeout", "30", "--", "https://example.com/a"]
    assert (document.format, document.url) == (dump, "https://example.com/a")
    assert document.content == "# Fetched\n\nhttps://example.com/a"
    assert response.raw[0] == {"url": "https://example.com/a", "dump": dump, "output": document.content}


def test_each_url_is_its_own_run_and_a_failed_one_is_a_failure_beside_the_rest(fake: Path, tmp_path: Path) -> None:
    with _router(fake) as router:
        response = router.extract(["https://example.com/a", "file:///etc/hosts", "ftp://example.com/b"])

    assert [d.url for d in response.documents] == ["https://example.com/a"]
    assert [(f.url, f.kind) for f in response.failures] == [
        ("file:///etc/hosts", "bad_request"),
        ("ftp://example.com/b", "bad_request"),
    ]
    assert len(calls(fake)) == 1, "a URL that is not http(s) never reaches the binary, which would read a local file"


@pytest.mark.parametrize(
    ("mode", "says"),
    [
        ("fail", "exited with code 1: Error: Failed to navigate: dns error"),
        ("binary", "not text"),
        ("pdf", "not text"),
        ("blank", "no content"),
    ],
)
def test_output_that_is_not_a_page_raises_rather_than_returning_an_empty_document(
    fake: Path, monkeypatch: pytest.MonkeyPatch, mode: str, says: str
) -> None:
    monkeypatch.setenv("FAKE_OBSCURA", mode)
    with _router(fake) as router, pytest.raises(ProviderError, match=says):
        router.extract("https://example.com/a")


def test_the_deadline_caps_the_fetch_and_a_hung_binary_is_killed(fake: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_OBSCURA", "hang")
    started = time.monotonic()
    with _router(fake) as router, pytest.raises(DeadlineExceededError):
        router.extract("https://example.com/a", timeout=1)

    assert time.monotonic() - started < 10
    run = calls(fake)[0]
    assert run["argv"][5] == "1", "the deadline's remainder, rounded up, is the binary's own --timeout"
    _assert_gone(int(run["pid"]))  # type: ignore[call-overload]


def test_with_no_deadline_the_binary_gets_its_default_and_a_grace_period(
    fake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_OBSCURA", "hang")
    monkeypatch.setattr(obscura, "OBSCURA_FETCH_TIMEOUT", 1.0)
    monkeypatch.setattr(obscura, "OBSCURA_EXIT_GRACE", 0.5)
    with _router(fake) as router, pytest.raises(ProviderError, match=r"did not finish within 1\.5s") as raised:
        router.extract("https://example.com/a")

    assert raised.value.kind == "timeout"
    assert calls(fake)[0]["argv"][5] == "1"
    _assert_gone(int(calls(fake)[0]["pid"]))  # type: ignore[call-overload]


async def test_the_async_call_reads_the_page_and_kills_a_hung_binary(
    fake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {PROVIDER_OBSCURA: {"executable_path": str(fake)}}
    async with AsyncWebRouter(PROVIDER_OBSCURA, provider_config=config) as router:
        document = (await router.extract("https://example.com/a")).documents[0]
        monkeypatch.setenv("FAKE_OBSCURA", "hang")
        started = time.monotonic()
        with pytest.raises(DeadlineExceededError):
            await router.extract("https://example.com/b", timeout=1)

    assert time.monotonic() - started < 10, "the hung process was killed, not waited out"

    assert document.content.endswith("https://example.com/a")
    _assert_gone(int(calls(fake)[1]["pid"]))  # type: ignore[call-overload]


def test_provider_options_are_refused_before_anything_runs(fake: Path) -> None:
    with _router(fake) as router, pytest.raises(UnsupportedParameterError, match="allow-private-network"):
        router.extract("https://example.com/a", provider_options={"allow_private_network": True})
    assert calls(fake) == []


def test_the_binary_is_found_by_argument_then_variable_then_path(fake: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(fake.parent))
    assert find_obscura() == str(fake)
    monkeypatch.chdir(fake.parent)
    assert os.path.dirname(find_obscura("./obscura")), "a relative path keeps its directory, never a PATH lookup"
    monkeypatch.setenv(ENV_OBSCURA_PATH, str(fake))
    assert find_obscura() == str(fake)
    not_executable = fake.parent / "notes.txt"
    not_executable.write_text("")
    for not_a_binary in (fake.parent / "missing", fake.parent, not_executable):
        with pytest.raises(ConfigurationError, match="not an executable file"):
            find_obscura(str(not_a_binary))
    monkeypatch.setenv(ENV_OBSCURA_PATH, "")
    monkeypatch.setenv("PATH", "")
    with pytest.raises(ConfigurationError, match=r"github\.com/h4ckf0r0day/obscura/releases"):
        find_obscura()


def test_it_is_opt_in_and_a_dotenv_file_cannot_aim_it(fake: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Listed once installed, yet in no ``auto`` chain, and its path is never read from a file the MCP server loads."""
    monkeypatch.setenv("PATH", "")
    assert PROVIDER_OBSCURA not in configured_extract_providers(), "not offered where it cannot run"
    monkeypatch.setenv(ENV_OBSCURA_PATH, str(fake))
    assert PROVIDER_OBSCURA in configured_extract_providers()
    assert PROVIDER_OBSCURA not in [name for name, _ in AutoExtractProvider.default_chain()]
    assert ENV_OBSCURA_PATH not in credential_variables()


def _assert_gone(pid: int) -> None:
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail(f"the obscura process {pid} was left running")


@pytest.mark.integration
@pytest.mark.skipif(not obscura_installed(), reason="no obscura binary on PATH or in OBSCURA_PATH")
def test_live_a_page_that_only_renders_in_javascript() -> None:
    with WebRouter(PROVIDER_OBSCURA) as router:
        document = router.extract("https://quotes.toscrape.com/js/").documents[0]
    assert "Albert Einstein" in document.content
