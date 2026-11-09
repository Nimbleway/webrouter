"""What search providers share on text: a body cut to a snippet, and domain filters as ``site:`` operators."""

from __future__ import annotations

import re
from collections.abc import Sequence

from web_router.constants import SNIPPET_LIMIT
from web_router.constants_search import EXCLUDE_SITE_OPERATOR, MSG_NOT_A_DOMAIN, SITE_DOMAIN_PATTERN, SITE_OPERATOR
from web_router.types import SearchDepth, SearchRequest

DOMAIN_LISTS = ("include_domains", "exclude_domains")
"""The two domain filters a search request carries."""

_SITE_DOMAIN = re.compile(SITE_DOMAIN_PATTERN)


def as_snippet(text: str) -> str:
    """Shorten body text to something the size of a snippet.

    For providers that return page content but no summary, so ``description``
    means the same thing everywhere. Breaks on whitespace to avoid cutting a
    word in half.
    """
    if len(text) <= SNIPPET_LIMIT:
        return text
    head = text[:SNIPPET_LIMIT]
    cut = head.rfind(" ")
    return (head[:cut] if cut > 0 else head).rstrip() + "\u2026"


def lite_body(text: str, depth: SearchDepth | None) -> str:
    """``text`` cut to a snippet when ``lite`` was asked for, else unchanged.

    For a backend whose own size setting does not bind -- a cap the vendor
    overshoots, or no setting at all -- so ``lite`` keeps its size whoever
    served it. The vendor's full text stays in ``raw``.
    """
    return as_snippet(text) if depth == "lite" else text


def site_operators(
    include: Sequence[str] | None,
    exclude: Sequence[str] | None = None,
    *,
    joiner: str = " OR ",
    exclude_format: str = EXCLUDE_SITE_OPERATOR,
) -> list[str]:
    """Domain filters as query operators: the included sites joined into one, then one per excluded site.

    Raises:
        ValueError: for a value that is not a host name (with an optional path),
            which would otherwise add its own operators to the query.
    """
    for domain in [*(include or ()), *(exclude or ())]:
        if not _SITE_DOMAIN.fullmatch(domain):
            raise ValueError(MSG_NOT_A_DOMAIN.format(value=domain))
    included = [joiner.join(SITE_OPERATOR.format(domain=domain) for domain in include)] if include else []
    return [*included, *(exclude_format.format(domain=domain) for domain in exclude or ())]


def refused_domains(
    request: SearchRequest, *, limit: int | None = None, both: Sequence[str] = DOMAIN_LISTS
) -> dict[str, list[str]]:
    """The domain lists a backend that takes one list at a time, of at most ``limit`` domains, refuses, by name.

    With both lists set, those in ``both`` are refused (the one the backend would ignore, or both); a list
    longer than ``limit`` is refused on its own.
    """
    lists = {name: getattr(request, name) or [] for name in DOMAIN_LISTS}
    refused = {name: lists[name] for name in both} if all(lists.values()) else {}
    over = {name: domains for name, domains in lists.items() if limit is not None and len(domains) > limit}
    return refused | over


def unsited_domains(request: SearchRequest, names: Sequence[str] = DOMAIN_LISTS) -> dict[str, list[str]]:
    """Each of ``names`` holding a value :func:`site_operators` would refuse, for a ``refused_options`` to report."""
    lists = {name: getattr(request, name) or [] for name in names}
    return {name: domains for name, domains in lists.items() if not all(map(_SITE_DOMAIN.fullmatch, domains))}
