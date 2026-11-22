"""A page the backend gave no title gets one from its content, whichever backend served it."""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.conftest import FakeExtractProvider
from web_router.providers._extract_title import page_title, titled
from web_router.types import ExtractedDocument, ExtractFormat, ExtractRequest, ExtractResponse

URL = "https://example.com/"
BODY = " Some words about the page." * 20
TITLE = "<title>Example &amp; Co</title>"
OG = '<meta content="Open &quot;Graph&quot;" property="og:title">'
H1 = '<h1 class="x">The <em>Heading</em></h1>'


@pytest.mark.parametrize(
    ("content", "format", "title"),
    [
        (f"<html><head>{TITLE}{OG}</head><body>{H1}</body></html>", "html", "Example & Co"),
        (f"<html><head>{OG}</head><body>{H1}</body></html>", "html", 'Open "Graph"'),
        ("<meta name='og:title' content='By name'><h1>H</h1>", "html", "By name"),
        (f"<html><body>{H1}<h1>Second</h1></body></html>", "html", "The Heading"),
        ("<html><body><p>No heading</p></body></html>", "html", ""),
        ("<meta property=og:title content=Bare><h1>H</h1>", "html", "Bare"),
        ('<meta property="og:title" content=""><h1>Fallback</h1>', "html", "Fallback"),
        ('<script>x = "<h1>Loading</h1>"</script><h1>Actual</h1>', "html", "Actual"),
        ("<h1>Actual<script>Hidden</script><style>Hidden</style></h1>", "html", "Actual"),
        ("<textarea><h1>Fake</h1></textarea><h1>Actual</h1>", "html", "Actual"),
        ("<h1> </h1><h1>Actual</h1>", "html", "Actual"),
        ('<meta property="og:title" content="A > B"><h1>Other</h1>', "html", "A > B"),
        ('<!-- <meta property="og:title" content="Hidden"> --><h1>Actual</h1>', "html", "Actual"),
        ("<!-- <title>Hidden</title><h1>Hidden</h1> --><h1>Actual</h1>", "html", "Actual"),
        ('<script>x = \'<meta property="og:title" content="Hidden">\'</script><h1>Actual</h1>', "html", "Actual"),
        ("Intro line\n\n# **Example** [Domain](https://x.test) #\n\n# Second", "markdown", "Example Domain"),
        ("Example Domain\n==============\n\ntext", "markdown", "Example Domain"),
        ("```\n# a comment in code\n```\n\n# Real", "markdown", "Real"),
        ("Text\n\n```python\n# an unclosed fence", "markdown", ""),
        ("```python\n```not-a-closer\n# Still code", "markdown", ""),
        ("````\n```\n# Still code\n````\n# Actual", "markdown", "Actual"),
        ("```\n~~~\n# Code", "markdown", ""),
        ("- ```\n  # code\n  ```\n# Real", "markdown", "Real"),
        ("1. + ~~~\n  # code\n  ~~~\n# Real", "markdown", "Real"),
        ("    ```\n# Real", "markdown", "Real"),
        ("# [Docs](https://example.com/a_(b))", "markdown", "Docs"),
        ("# 100 * 20", "markdown", "100 * 20"),
        ("# `100 * 20`", "markdown", "100 * 20"),
        (r"# Escaped \*literal\*", "markdown", "Escaped *literal*"),
        ("# **bold [link](https://example.test)**", "markdown", "bold link"),
        ("# [**bold** link](https://example.test)", "markdown", "bold link"),
        ("# \n# Actual", "markdown", "Actual"),
        ("# ###\n# Actual", "markdown", "Actual"),
        ("# `[Docs](https://example.test)`", "markdown", "[Docs](https://example.test)"),
        ("#hashtag\n\n## Only an H2", "markdown", ""),
        ("# Looks like a heading", "text", ""),
        ("# Format unset", None, "Format unset"),
    ],
)
def test_the_title_comes_from_title_then_og_title_then_the_first_h1(
    content: str, format: ExtractFormat | None, title: str
) -> None:
    assert page_title(ExtractedDocument(url=URL, content=content, format=format)) == title


def test_a_title_the_backend_gave_is_kept_and_the_h1_stays_in_the_content() -> None:
    given = ExtractedDocument(url=URL, title="Backend's", content="# Other", format="markdown")
    assert titled(given) is given

    filled = titled(given.model_copy(update={"title": "  "}))
    assert (filled.title, filled.content) == ("Other", "# Other")


class _Untitled(FakeExtractProvider):
    """A backend that, like Nimble, names no title."""

    def _respond(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        content = f"# Example Domain\n\n{BODY}"
        documents = [ExtractedDocument(url=url, content=content, format="markdown") for url in urls]
        return ExtractResponse(provider=self.name, documents=documents)


def test_an_explicit_provider_call_fills_the_title_and_keeps_the_h1() -> None:
    document = _Untitled().extract(ExtractRequest(urls=[URL])).documents[0]

    assert document.title == "Example Domain"
    assert document.content.startswith("# Example Domain")


@pytest.mark.parametrize("case", ["meta", "spaces", "links", "emphasis", "large_atx", "large_setext"])
def test_long_markup_does_not_stall_extraction(case: str) -> None:
    script = """
import sys
from web_router.providers._extract_title import page_title
from web_router.types import ExtractedDocument
cases = {
    'meta': ('<meta ' + 'a' * 100_000 + '><h1>Actual</h1>', 'html', 'Actual'),
    'spaces': ('# a' + ' ' * 100_000 + 'b', 'markdown', ''),
    'links': ('# ' + '[x](' * 25_000, 'markdown', ''),
    'emphasis': ('# ' + '*a*X' * 25_000, 'markdown', ''),
    'large_atx': ('# ' + '*a*X' * 125_000 + '\\n# Real', 'markdown', 'Real'),
    'large_setext': ('*a*X' * 125_000 + '\\n===\\n# Real', 'markdown', 'Real'),
}
content, format, expected = cases[sys.argv[1]]
assert page_title(ExtractedDocument(url='https://example.com/', content=content, format=format)) == expected
"""
    subprocess.run([sys.executable, "-c", script, case], check=True, timeout=5)


@pytest.mark.parametrize("setext", [False, True])
@pytest.mark.parametrize("length", [1_000, 1_001])
def test_large_heading_candidates_are_skipped_before_inline_parsing(
    monkeypatch: pytest.MonkeyPatch, setext: bool, length: int
) -> None:
    from web_router.providers import _title_markdown

    def bounded_inline(text: str) -> str:
        assert len(text) <= 1_000
        return text

    monkeypatch.setattr(_title_markdown, "_inline", bounded_inline)
    heading = "a" * length
    content = f"{heading}\n===\n# Real" if setext else f"# {heading}\n# Real"
    assert _title_markdown.page_title(content) == (heading if length == 1_000 else "Real")
