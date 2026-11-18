"""The Oxylabs Web API's scraper list: which scrapers it has, how each is run, and each as a live data source.

A scraper is named by its path after ``/v1/scrape/``. ``GET /v1/scrapers``
lists each under the delivery modes it is offered in -- realtime
(``/v1/scrape/...``), as a job (``/v1/async/scrape/...``) and in batches --
so one name can appear up to three times.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from web_router.constants_live_data_vendors import (
    OXYLABS_ASYNC,
    OXYLABS_ASYNC_PREFIX,
    OXYLABS_BATCH,
    OXYLABS_BATCH_PREFIX,
    OXYLABS_DOCS_URL,
    OXYLABS_SCRAPE_PREFIX,
    OXYLABS_SOURCE_AS_JOB,
    OXYLABS_SOURCE_DESCRIPTION,
    OXYLABS_SYNC,
)
from web_router.types_live_data import LiveDataSource

_MEDIA = "media"


@dataclass
class Scraper:
    """One dedicated scraper: its name, and the delivery modes the listing offers it in."""

    name: str
    modes: set[str] = field(default_factory=set)

    @property
    def realtime(self) -> bool:
        return OXYLABS_SYNC in self.modes

    @property
    def path(self) -> str:
        """Where a run is sent: the realtime endpoint, or the async one for a scraper that has no other."""
        return f"{OXYLABS_SCRAPE_PREFIX if self.realtime else OXYLABS_ASYNC_PREFIX}/{self.name}"


def _named_by(path: str) -> tuple[str, str] | None:
    """The source a listed path is, and its delivery mode; ``None`` for a path that is no scraper's."""
    for prefix, mode in (
        (OXYLABS_BATCH_PREFIX, OXYLABS_BATCH),
        (OXYLABS_ASYNC_PREFIX, OXYLABS_ASYNC),
        (OXYLABS_SCRAPE_PREFIX, OXYLABS_SYNC),
    ):
        if path.startswith(prefix + "/"):
            return path[len(prefix) + 1 :].strip("/"), mode
    return None


def catalogue(payload: Any) -> dict[str, Scraper]:
    """Every dedicated scraper in a ``GET /v1/scrapers`` answer, in its order, one per name."""
    paths = payload.get("scrapers") if isinstance(payload, dict) else None
    known: dict[str, Scraper] = {}
    for path in paths if isinstance(paths, list) else []:
        found = _named_by(path) if isinstance(path, str) else None
        if found and found[0]:
            name, mode = found
            known.setdefault(name, Scraper(name)).modes.add(mode)
    # A name listed only as a batch has no single-run endpoint to call.
    return {name: scraper for name, scraper in known.items() if scraper.modes - {OXYLABS_BATCH}}


def source_name(source: str) -> str:
    """A source as named, or as a path: ``/v1/scrape/amazon/product`` is ``amazon/product``."""
    found = _named_by(source.strip())
    return found[0] if found else source.strip().strip("/")


def listed(provider: str, scraper: Scraper) -> LiveDataSource:
    """A scraper as a live data source: its site, the kind of page, and how it is run."""
    parts = scraper.name.split("/")
    # Media scrapers name their site second: media/youtube/metadata reads YouTube.
    at = 1 if parts[0] == _MEDIA and len(parts) > 1 else 0
    mode = "" if scraper.realtime else OXYLABS_SOURCE_AS_JOB
    return LiveDataSource(
        provider=provider,
        name=scraper.name,
        description=OXYLABS_SOURCE_DESCRIPTION.format(name=scraper.name, mode=mode, docs=OXYLABS_DOCS_URL),
        domain=parts[at],
        entity_type="/".join(parts[at + 1 :]) or None,
        published=True,
        extra={"endpoint": scraper.path, "modes": sorted(scraper.modes)},
    )
