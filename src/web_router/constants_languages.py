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

ISO_639_1 = frozenset(
    {
        "aa", "ab", "ae", "af", "ak", "am", "an", "ar", "as", "av", "ay", "az", "ba", "be",
        "bg", "bh", "bi", "bm", "bn", "bo", "br", "bs", "ca", "ce", "ch", "co", "cr", "cs",
        "cu", "cv", "cy", "da", "de", "dv", "dz", "ee", "el", "en", "eo", "es", "et", "eu",
        "fa", "ff", "fi", "fj", "fo", "fr", "fy", "ga", "gd", "gl", "gn", "gu", "gv", "ha",
        "he", "hi", "ho", "hr", "ht", "hu", "hy", "hz", "ia", "id", "ie", "ig", "ii", "ik",
        "io", "is", "it", "iu", "ja", "jv", "ka", "kg", "ki", "kj", "kk", "kl", "km", "kn",
        "ko", "kr", "ks", "ku", "kv", "kw", "ky", "la", "lb", "lg", "li", "ln", "lo", "lt",
        "lu", "lv", "mg", "mh", "mi", "mk", "ml", "mn", "mr", "ms", "mt", "my", "na", "nb",
        "nd", "ne", "ng", "nl", "nn", "no", "nr", "nv", "ny", "oc", "oj", "om", "or", "os",
        "pa", "pi", "pl", "ps", "pt", "qu", "rm", "rn", "ro", "ru", "rw", "sa", "sc", "sd",
        "se", "sg", "si", "sk", "sl", "sm", "sn", "so", "sq", "sr", "ss", "st", "su", "sv",
        "sw", "ta", "te", "tg", "th", "ti", "tk", "tl", "tn", "to", "tr", "ts", "tt", "tw",
        "ty", "ug", "uk", "ur", "uz", "ve", "vi", "vo", "wa", "wo", "xh", "yi", "yo", "za",
        "zh", "zu",
    }
)  # fmt: skip
"""The 184 ISO 639-1 two-letter language codes, lower-case: what a backend that documents its language field as
ISO 639-1 (WebIQ) takes. A well-formed pair such as ``zz`` that is not on it is refused."""
