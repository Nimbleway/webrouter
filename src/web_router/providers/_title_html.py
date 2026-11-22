"""HTML title candidates, parsed without treating comments or scripts as markup."""

from __future__ import annotations

from html.parser import HTMLParser

_H1_ENDS = frozenset(
    {"h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "section", "article", "header", "ul", "ol", "table"}
)


def page_title(content: str) -> str:
    """The first nonempty title candidate, in title / Open Graph / H1 order."""
    parser = _Titles()
    parser.feed(content)
    parser.close()
    return next((title for text in (parser.title, parser.og, parser.h1) if (title := " ".join(text.split()))), "")


class _Titles(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.og = ""
        self.h1 = ""
        self._capture = ""
        self._parts: list[str] = []
        self._hidden = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._hidden:
            return
        if self._capture == "h1" and tag in _H1_ENDS:
            self.handle_endtag("h1")
        if tag == "br" and self._capture:
            self._parts.append(" ")
        if tag in {"script", "style", "textarea"}:
            self._hidden = tag
        elif tag == "meta" and not self.og:
            attributes = dict(attrs)
            names = (attributes.get("property") or "", attributes.get("name") or "")
            if any(name.lower() == "og:title" for name in names):
                self.og = (attributes.get("content") or "").strip()
        elif tag in {"title", "h1"} and not self._capture and not getattr(self, tag):
            self._capture = tag
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "body" and self._capture == "h1" and not self._hidden:
            self.handle_endtag("h1")
        if self._hidden:
            if tag == self._hidden:
                self._hidden = ""
        elif tag == self._capture:
            setattr(self, tag, "".join(self._parts).strip())
            self._capture = ""

    def handle_data(self, data: str) -> None:
        if self._capture and not self._hidden:
            self._parts.append(data)
