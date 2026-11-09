"""A locale onto the language a backend takes, and the fixed lists You.com and Brave search hold it to."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from web_router.constants_languages import LANGUAGE_ALIASES, LANGUAGE_TAGS
from web_router.types import SearchRequest


def language_code(locale: str) -> str:
    """The language of a locale, lowercase: ``en-GB`` and ``en_gb`` both give ``en``."""
    return locale.replace("_", "-").split("-")[0].lower()


def language_tag(locale: str) -> str | None:
    """The listed tag ``locale`` means, upper-case, or ``None`` when it means none of them.

    The whole tag is tried first (``en-GB``, ``zh-Hans``), then a region's script
    (``zh-TW``), then the language alone (``fr-CA`` is ``FR``). The language alone
    is never one of two listed tags: ``pt`` stays ``None`` rather than becoming
    ``PT-BR`` or ``PT-PT``.
    """
    parts = locale.replace("_", "-").upper().split("-")
    for candidate in ("-".join(parts), "-".join(parts[:2]), parts[0]):
        candidate = LANGUAGE_ALIASES.get(candidate, candidate)
        if candidate in LANGUAGE_TAGS:
            return candidate
    return None


def off_the_lists(request: SearchRequest, countries: Collection[str]) -> dict[str, Any]:
    """A ``country`` outside ``countries`` (upper-case codes) or a ``locale`` off the language list, by name."""
    refused: dict[str, Any] = {}
    if request.country and request.country.upper() not in countries:
        refused["country"] = request.country
    if request.locale and language_tag(request.locale) is None:
        refused["locale"] = request.locale
    return refused
