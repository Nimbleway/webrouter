"""A locale onto the fixed language list You.com and Brave search take."""

from __future__ import annotations

from web_router.constants_languages import LANGUAGE_ALIASES, LANGUAGE_TAGS


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
