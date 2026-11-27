"""Verify YouTube caption extraction through both routers with a fake yt-dlp."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from web_router import AsyncWebRouter, ProviderError, UnsupportedFormatError, UnsupportedParameterError, WebRouter
from web_router.exceptions import ConfigurationError
from web_router.registry import configured_extract_providers

VIDEO = "https://youtu.be/jNQXAC9IVRw?si=example"
BLOCKED = "https://youtube.com.example/watch?v=jNQXAC9IVRw"
MISSING = "https://www.youtube.com/shorts/abcdefghijk"

_FAKE = """
import json
import pathlib
import re
import sys

args = sys.argv[1:]
url = args[-1]
assert args[-2] == "--" and "--ignore-config" in args and "--skip-download" in args
assert "--no-plugin-dirs" in args and "--no-playlist" in args
assert url.startswith("https://www.youtube.com/watch?v=")
if url.endswith("abcdefghijk"):
    sys.exit(0)
assert url.endswith("jNQXAC9IVRw")
language = re.sub(r"[^a-zA-Z-]", "", args[args.index("--sub-langs") + 1])
output = args[args.index("-o") + 1].replace("%(ext)s", language + ".json3")
events = [
    {"segs": [{"utf8": "hello "}, {"utf8": "world"}], "tStartMs": 1000},
    {"segs": [{"utf8": "hello world"}, {"utf8": "\\nnext line"}]},
    {"wWinId": 1},
]
pathlib.Path(output).write_text(json.dumps({"events": events}), encoding="utf-8")
print(json.dumps("Example video"))
"""


@pytest.fixture
def downloader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name != "posix":
        pytest.skip("fixture uses a Unix executable script")
    command = tmp_path / "yt-dlp"
    command.write_text(f"#!{sys.executable}\n{_FAKE}", encoding="utf-8")
    command.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))


def test_captions_via_router(downloader: None) -> None:
    assert "youtube-captions" in configured_extract_providers()
    with WebRouter() as router:
        response = router.extract(VIDEO, provider="youtube-captions", provider_options={"language": "pt-BR"})
    doc = response.documents[0]
    assert (doc.url, doc.title, doc.format, doc.content) == (VIDEO, "Example video", "text", "hello world\nnext line")
    assert doc.extra == {"language": "pt-BR"}
    assert response.raw[0]["events"][0]["tStartMs"] == 1000


@pytest.mark.asyncio
async def test_async_batch_keeps_successes_and_reports_failures(downloader: None) -> None:
    async with AsyncWebRouter() as router:
        response = await router.extract([VIDEO, BLOCKED, MISSING], provider="youtube-captions")
    assert [doc.url for doc in response.documents] == [VIDEO]
    assert {failure.url: failure.kind for failure in response.failures} == {
        BLOCKED: "bad_request",
        MISSING: "not_found",
    }


def test_missing_and_unsupported_options(downloader: None) -> None:
    with WebRouter() as router:
        with pytest.raises(UnsupportedFormatError):
            router.extract(VIDEO, provider="youtube-captions", format="html")
        with pytest.raises(UnsupportedParameterError):
            router.extract(VIDEO, provider="youtube-captions", provider_options={"language": "all,-live_chat"})
        with pytest.raises(UnsupportedParameterError):
            router.extract(VIDEO, provider="youtube-captions", provider_options={"cookies": "cookies.txt"})
        with pytest.raises(ProviderError, match="no captions available"):
            router.extract(MISSING, provider="youtube-captions")


def test_missing_executable_reports_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    assert "youtube-captions" not in configured_extract_providers()
    with WebRouter() as router, pytest.raises(ConfigurationError, match="Install yt-dlp"):
        router.extract(VIDEO, provider="youtube-captions")
