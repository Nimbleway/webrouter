"""The method table the ``features`` router reads: one method per combination of a prompt's four features.

Experimental. Learned once, by hand, from a benchmark of 1,920 labelled web requests, each tagged with the method
that served it best: the table holds the majority method per combination, backing off to the leading features
where a combination has under 20 rows (``# backoff``). It never picks ``answer``, which that benchmark did not
measure.
"""

from __future__ import annotations

from typing import NamedTuple

from web_router.routing.contract import ToolName
from web_router.routing.features.sensors import Verb


class Features(NamedTuple):
    """How the ``features`` router reads a prompt: :func:`~web_router.routing.features.read_features`."""

    verb: Verb
    has_url: bool
    plural: bool
    kw_research: bool

    @property
    def tool(self) -> ToolName:
        """The method the table picks for these features."""
        return FEATURE_MAPPING[self]


FEATURE_MAPPING: dict[Features, ToolName] = {
    Features(Verb.QUESTION, has_url=True, plural=True, kw_research=True): "extract",  # backoff
    Features(Verb.QUESTION, has_url=True, plural=True, kw_research=False): "extract",  # backoff
    Features(Verb.QUESTION, has_url=True, plural=False, kw_research=True): "extract",  # backoff
    Features(Verb.QUESTION, has_url=True, plural=False, kw_research=False): "extract",
    Features(Verb.QUESTION, has_url=False, plural=True, kw_research=True): "research",  # backoff
    Features(Verb.QUESTION, has_url=False, plural=True, kw_research=False): "research",
    Features(Verb.QUESTION, has_url=False, plural=False, kw_research=True): "research",
    Features(Verb.QUESTION, has_url=False, plural=False, kw_research=False): "search",
    Features(Verb.EXTRACT, has_url=True, plural=True, kw_research=True): "extract",  # backoff
    Features(Verb.EXTRACT, has_url=True, plural=True, kw_research=False): "extract",
    Features(Verb.EXTRACT, has_url=True, plural=False, kw_research=True): "extract",  # backoff
    Features(Verb.EXTRACT, has_url=True, plural=False, kw_research=False): "extract",
    Features(Verb.EXTRACT, has_url=False, plural=True, kw_research=True): "extract",  # backoff
    Features(Verb.EXTRACT, has_url=False, plural=True, kw_research=False): "extract",  # backoff
    Features(Verb.EXTRACT, has_url=False, plural=False, kw_research=True): "extract",  # backoff
    Features(Verb.EXTRACT, has_url=False, plural=False, kw_research=False): "extract",  # backoff
    Features(Verb.ACT, has_url=True, plural=True, kw_research=True): "browse",  # backoff
    Features(Verb.ACT, has_url=True, plural=True, kw_research=False): "browse",  # backoff
    Features(Verb.ACT, has_url=True, plural=False, kw_research=True): "browse",  # backoff
    Features(Verb.ACT, has_url=True, plural=False, kw_research=False): "browse",  # backoff
    Features(Verb.ACT, has_url=False, plural=True, kw_research=True): "browse",  # backoff
    Features(Verb.ACT, has_url=False, plural=True, kw_research=False): "browse",  # backoff
    Features(Verb.ACT, has_url=False, plural=False, kw_research=True): "browse",  # backoff
    Features(Verb.ACT, has_url=False, plural=False, kw_research=False): "browse",  # backoff
    Features(Verb.NAVIGATE, has_url=True, plural=True, kw_research=True): "browse",  # backoff
    Features(Verb.NAVIGATE, has_url=True, plural=True, kw_research=False): "browse",
    Features(Verb.NAVIGATE, has_url=True, plural=False, kw_research=True): "browse",  # backoff
    Features(Verb.NAVIGATE, has_url=True, plural=False, kw_research=False): "browse",
    Features(Verb.NAVIGATE, has_url=False, plural=True, kw_research=True): "browse",  # backoff
    Features(Verb.NAVIGATE, has_url=False, plural=True, kw_research=False): "browse",
    Features(Verb.NAVIGATE, has_url=False, plural=False, kw_research=True): "browse",  # backoff
    Features(Verb.NAVIGATE, has_url=False, plural=False, kw_research=False): "browse",
    Features(Verb.COMPILE, has_url=True, plural=True, kw_research=True): "research",  # backoff
    Features(Verb.COMPILE, has_url=True, plural=True, kw_research=False): "research",  # backoff
    Features(Verb.COMPILE, has_url=True, plural=False, kw_research=True): "research",  # backoff
    Features(Verb.COMPILE, has_url=True, plural=False, kw_research=False): "research",  # backoff
    Features(Verb.COMPILE, has_url=False, plural=True, kw_research=True): "research",  # backoff
    Features(Verb.COMPILE, has_url=False, plural=True, kw_research=False): "research",
    Features(Verb.COMPILE, has_url=False, plural=False, kw_research=True): "research",  # backoff
    Features(Verb.COMPILE, has_url=False, plural=False, kw_research=False): "research",  # backoff
    Features(Verb.ANALYZE, has_url=True, plural=True, kw_research=True): "research",  # backoff
    Features(Verb.ANALYZE, has_url=True, plural=True, kw_research=False): "research",  # backoff
    Features(Verb.ANALYZE, has_url=True, plural=False, kw_research=True): "research",  # backoff
    Features(Verb.ANALYZE, has_url=True, plural=False, kw_research=False): "extract",
    Features(Verb.ANALYZE, has_url=False, plural=True, kw_research=True): "research",  # backoff
    Features(Verb.ANALYZE, has_url=False, plural=True, kw_research=False): "research",  # backoff
    Features(Verb.ANALYZE, has_url=False, plural=False, kw_research=True): "research",  # backoff
    Features(Verb.ANALYZE, has_url=False, plural=False, kw_research=False): "research",  # backoff
    Features(Verb.OTHER, has_url=True, plural=True, kw_research=True): "extract",  # backoff
    Features(Verb.OTHER, has_url=True, plural=True, kw_research=False): "extract",
    Features(Verb.OTHER, has_url=True, plural=False, kw_research=True): "extract",  # backoff
    Features(Verb.OTHER, has_url=True, plural=False, kw_research=False): "extract",
    Features(Verb.OTHER, has_url=False, plural=True, kw_research=True): "research",
    Features(Verb.OTHER, has_url=False, plural=True, kw_research=False): "research",
    Features(Verb.OTHER, has_url=False, plural=False, kw_research=True): "search",  # backoff
    Features(Verb.OTHER, has_url=False, plural=False, kw_research=False): "search",
}
