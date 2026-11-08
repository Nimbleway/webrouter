"""The fixed language list You.com and Brave search take, and how a locale reaches it.

You.com's ``language`` and Brave's ``search_lang`` list the same 51 tags, You.com
in upper case and Brave in lower (Brave also takes ``jp`` beside ``ja``). Any
other value is a 422, so a locale is translated onto the list here, and one that
lands on none of it is refused before the call.
"""

from __future__ import annotations

LANGUAGE_TAGS = frozenset(
    {
        "AR", "EU", "BN", "BG", "CA", "ZH-HANS", "ZH-HANT", "HR", "CS", "DA", "NL", "EN", "EN-GB",
        "ET", "FI", "FR", "GL", "DE", "EL", "GU", "HE", "HI", "HU", "IS", "IT", "JA", "KN", "KO",
        "LV", "LT", "MS", "ML", "MR", "NB", "PL", "PT-BR", "PT-PT", "PA", "RO", "RU", "SR", "SK",
        "SL", "ES", "SV", "TA", "TE", "TH", "TR", "UK", "VI",
    }
)  # fmt: skip
"""You.com's ``language`` values (you.com/docs/api-reference/search/v1-search); Brave's ``search_lang`` repeats them."""

LANGUAGE_ALIASES: dict[str, str] = {
    "ZH-CN": "ZH-HANS",
    "ZH-SG": "ZH-HANS",
    "ZH-TW": "ZH-HANT",
    "ZH-HK": "ZH-HANT",
    "ZH-MO": "ZH-HANT",
    "NO": "NB",
    "IW": "HE",
}
"""A locale whose tag is not listed but means one that is: a Chinese region's script, Norwegian's Bokmal, Hebrew's
old code. ``pt`` and ``zh`` alone stay unlisted: each could be either of two tags, so they are refused, not guessed."""
