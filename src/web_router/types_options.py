"""The levels shared options are set at: how much page text, how much work, how recent.

Kept apart so ``types.py`` stops growing. ``types.py`` imports these back, so
``web_router.types.SearchDepth`` and the rest still resolve; this module
imports nothing of the package's, so any other module may import it.
"""

from __future__ import annotations

from typing import Literal

SearchDepth = Literal["lite", "fast", "deep"]
"""How much page content to retrieve per result.

- ``lite``: title, URL and description only — cheapest, best at high volume.
- ``fast``: a truncated page body, enough for most agent reasoning.
- ``deep``: full page content; slowest and most expensive.

Providers express this differently; each one maps these three levels onto its
own vocabulary.
"""

ResearchEffort = Literal["low", "medium", "high", "max"]
"""How much work a research run, or a search, should do before answering.

- ``low``: a quick pass, closest in cost to a deep search.
- ``medium``: the default shape of an agentic run.
- ``high``: several rounds of gathering and cross-checking.
- ``max``: everything the backend will spend on one question.

Backends name these tiers differently -- processors, effort levels, reasoning
budgets -- and some offer fewer than four. Each one maps these onto its own
vocabulary, and a backend with coarser tiers maps more than one level onto the
same setting rather than inventing a distinction it does not have.

A search reads the same four levels against the backend's own search tiers, as
:data:`SEARCH_EFFORT_DESCRIPTION` anchors them.
"""

Recency = Literal["day", "week", "month", "year"]
"""How recent a source must be: one the backend dates within the last day, week, month or year."""

RECENCY_DESCRIPTION = (
    "Only sources the backend dates within the last day, week, month or year, by publication date where it has one."
)
"""What ``recency`` means on every method that takes it.

"Dates", not "published": some backends also count a page's last-updated date,
and some keep pages they hold no date for.
"""

SEARCH_EFFORT_DESCRIPTION = (
    "How hard the backend works to find results: 'medium' is what it does when unset, 'low' its fastest tier, "
    "'max' its most thorough, and 'high' sits between them or shares 'max''s setting. A backend with fewer tiers "
    "serves several levels with one setting rather than refusing any. Higher effort costs more and takes longer, "
    "by a vendor-specific amount; leave it unset unless needed."
)
"""What search's ``effort`` means, anchored so a level means the same on every backend that maps it."""
