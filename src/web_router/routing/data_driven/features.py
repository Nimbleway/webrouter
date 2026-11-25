"""The five things the data-driven router reads in a request's text, each a short string.

Every feature is a regular expression over the text: it takes microseconds, calls nothing, and gives the same
value for the same text. The expressions live in :mod:`.patterns`, which is never edited by hand.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from web_router.routing.data_driven.patterns import (
    KW_RESEARCH_RE,
    PLURAL_RE,
    ROOT_PATH_RE,
    UI_ACTION_RE,
    URL_PARTS_RE,
    VERB_RE,
    WH_RE,
)


def verb(text: str) -> str:
    """The request's opening move, after polite openers: the class of its leading verb, a question, or ``other``."""
    for name, pattern in VERB_RE:
        if pattern.match(text):
            return name
    return "question" if WH_RE.match(text) else "other"


def url_target(text: str) -> str:
    """Where the request's URL points: a site's root (``site``), one page on it (``page``), or no URL (``none``)."""
    found = URL_PARTS_RE.search(text)
    if not found:
        return "none"
    path = (found.group("path") or "").rstrip(".,)")
    return "site" if ROOT_PATH_RE.match(path) else "page"


def plural(text: str) -> str:
    """Whether the request asks for many items (all, every, list, top 10) and not one."""
    return "yes" if PLURAL_RE.search(text) else "no"


def kw_research(text: str) -> str:
    """Whether the request holds a research word: report, compare, in-depth, overview and the like."""
    return "yes" if KW_RESEARCH_RE.search(text) else "no"


def ui_action(text: str) -> str:
    """Whether the request words an action on a page: click, log in, add to cart, fill the form."""
    return "yes" if UI_ACTION_RE.search(text) else "no"


FEATURES: dict[str, Callable[[str], str]] = {
    "verb": verb,
    "url_target": url_target,
    "plural": plural,
    "kw_research": kw_research,
    "ui_action": ui_action,
}
"""Every feature a tool table may name, by the name the table uses."""


def featurize(text: str, names: Sequence[str]) -> list[str]:
    """``text``'s value for each feature in ``names``, in that order."""
    return [FEATURES[name](text) for name in names]
