"""Tavily search: the options it maps, and the country names its ``country`` takes.

Kept apart from :mod:`web_router.constants`, which every other provider's
settings share, so that file stops growing. The env var, depth table and
extract settings stay there.
"""

from __future__ import annotations

from typing import Any

from web_router.types_options import Recency

# docs.tavily.com/documentation/api-reference/endpoint/search, read 2026-11-09.

TAVILY_PARAM_NAMES = {
    "max_results": "max_results",
    "include_domains": "include_domains",
    "exclude_domains": "exclude_domains",
}
"""Normalized parameter -> Tavily field, the same names. ``search_depth`` maps to two fields; ``country``,
``locale`` and ``recency`` are translated, below. Tavily takes up to 300 included and 150 excluded domains and
answers a longer list itself."""

TAVILY_SEARCH_PARAMETERS = frozenset({*TAVILY_PARAM_NAMES, "search_depth", "country", "locale", "recency"})
"""``effort`` is left out: Tavily's latency tiers live in the same ``search_depth`` field our depth uses."""

TAVILY_COUNTRIES: dict[str, str] = {
    "AD": "andorra", "AE": "united arab emirates", "AF": "afghanistan", "AL": "albania", "AM": "armenia",
    "AO": "angola", "AR": "argentina", "AT": "austria", "AU": "australia", "AZ": "azerbaijan",
    "BA": "bosnia and herzegovina", "BB": "barbados", "BD": "bangladesh", "BE": "belgium", "BF": "burkina faso",
    "BG": "bulgaria", "BH": "bahrain", "BI": "burundi", "BJ": "benin", "BN": "brunei", "BO": "bolivia",
    "BR": "brazil", "BS": "bahamas", "BT": "bhutan", "BW": "botswana", "BY": "belarus", "BZ": "belize",
    "CA": "canada", "CF": "central african republic", "CG": "congo", "CH": "switzerland", "CL": "chile",
    "CM": "cameroon", "CN": "china", "CO": "colombia", "CR": "costa rica", "CU": "cuba", "CV": "cape verde",
    "CY": "cyprus", "CZ": "czech republic", "DE": "germany", "DJ": "djibouti", "DK": "denmark",
    "DO": "dominican republic", "DZ": "algeria", "EC": "ecuador", "EE": "estonia", "EG": "egypt", "ER": "eritrea",
    "ES": "spain", "ET": "ethiopia", "FI": "finland", "FJ": "fiji", "FR": "france", "GA": "gabon",
    "GB": "united kingdom", "GE": "georgia", "GH": "ghana", "GM": "gambia", "GN": "guinea",
    "GQ": "equatorial guinea", "GR": "greece", "GT": "guatemala", "HN": "honduras", "HR": "croatia", "HT": "haiti",
    "HU": "hungary", "ID": "indonesia", "IE": "ireland", "IL": "israel", "IN": "india", "IQ": "iraq", "IR": "iran",
    "IS": "iceland", "IT": "italy", "JM": "jamaica", "JO": "jordan", "JP": "japan", "KE": "kenya",
    "KG": "kyrgyzstan", "KH": "cambodia", "KM": "comoros", "KP": "north korea", "KR": "south korea",
    "KW": "kuwait", "KZ": "kazakhstan", "LB": "lebanon", "LI": "liechtenstein", "LK": "sri lanka", "LR": "liberia",
    "LS": "lesotho", "LT": "lithuania", "LU": "luxembourg", "LV": "latvia", "LY": "libya", "MA": "morocco",
    "MC": "monaco", "MD": "moldova", "ME": "montenegro", "MG": "madagascar", "MK": "north macedonia", "ML": "mali",
    "MM": "myanmar", "MN": "mongolia", "MR": "mauritania", "MT": "malta", "MU": "mauritius", "MV": "maldives",
    "MW": "malawi", "MX": "mexico", "MY": "malaysia", "MZ": "mozambique", "NA": "namibia", "NE": "niger",
    "NG": "nigeria", "NI": "nicaragua", "NL": "netherlands", "NO": "norway", "NP": "nepal", "NZ": "new zealand",
    "OM": "oman", "PA": "panama", "PE": "peru", "PG": "papua new guinea", "PH": "philippines", "PK": "pakistan",
    "PL": "poland", "PT": "portugal", "PY": "paraguay", "QA": "qatar", "RO": "romania", "RS": "serbia",
    "RU": "russia", "RW": "rwanda", "SA": "saudi arabia", "SD": "sudan", "SE": "sweden", "SG": "singapore",
    "SI": "slovenia", "SK": "slovakia", "SN": "senegal", "SO": "somalia", "SS": "south sudan", "SV": "el salvador",
    "SY": "syria", "TD": "chad", "TG": "togo", "TH": "thailand", "TJ": "tajikistan", "TM": "turkmenistan",
    "TN": "tunisia", "TR": "turkey", "TT": "trinidad and tobago", "TW": "taiwan", "TZ": "tanzania",
    "UA": "ukraine", "UG": "uganda", "US": "united states", "UY": "uruguay", "UZ": "uzbekistan", "VE": "venezuela",
    "VN": "vietnam", "YE": "yemen", "ZA": "south africa", "ZM": "zambia", "ZW": "zimbabwe",
}  # fmt: skip
"""ISO code -> the lowercase English name Tavily's ``country`` takes, for all 166 it lists; it rejects the code.

A boost toward the country, not a filter, and only on ``topic`` general (Tavily's default)."""

TAVILY_GENERAL_TOPIC = "general"

TAVILY_LANGUAGE_FILTER: dict[str, Any] = {"filter_by_language": True}
"""Sent with ``language``: without it a language only boosts ranking, and ``locale`` is a filter."""

TAVILY_RECENCY_FILTER: dict[str, Any] = {"filter_by_published_date": True}
"""Sent with ``time_range``: without it a page with no date it can detect is kept. A forwarded field: the SDK's
``search`` does not declare it and passes it into the body through ``**kwargs``."""

TAVILY_TIME_RANGE: dict[Recency, str] = {"day": "day", "week": "week", "month": "month", "year": "year"}
"""``time_range``, by publish date or last updated date: Tavily's own values, one for one."""

MSG_TAVILY_COUNTRY = (
    "Tavily boosts only the countries it lists (166, by name), and only on topic general. Ask for one of those "
    "without another topic, leave country unset, or name another provider -- see the search table at https://docs.webrouter.net/providers#search."
)
