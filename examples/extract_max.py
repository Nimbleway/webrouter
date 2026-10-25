"""``max``: every extract backend at once, and the first document back wins.

`auto` tries backends one after another; `max` sends the same URL to all of
them at the same time and returns whichever answers first. It is the fastest
read any backend can give, and it bills one fetch per backend that was
started, per URL -- so it is never the default. A `hedge` keeps most one-URL
calls to one fetch: the first backend runs alone for that long, and the rest
join only if it has not answered.

Needs no key: with none, the race is between the keyless backends. Every key
you hold adds a runner.

Run it with::

    make setup   # in your webrouter clone: every extra
    uv run --env-file .env python examples/extract_max.py
"""

from __future__ import annotations

import time

from web_router import Provider, WebRouter

URL = "https://en.wikipedia.org/wiki/Web_scraping"


def race(router: WebRouter, label: str) -> None:
    started = time.perf_counter()
    response = router.extract(URL, format="markdown")
    elapsed = time.perf_counter() - started
    print(f"{label:<22} {response.provider:<10} {elapsed:5.2f}s  {len(response.documents[0].content):>7} chars")


def main() -> None:
    with WebRouter(Provider.AUTO) as sequential, WebRouter(Provider.MAX) as everyone:
        print("members raced:", ", ".join(everyone.get_extract_provider().members))  # type: ignore[attr-defined]
        race(sequential, "auto, one at a time")
        race(everyone, "max, all at once")

    # The first member runs alone for 0.8s; the rest start only if it is still silent.
    with WebRouter(Provider.MAX, provider_config={"max": {"hedge": 0.8}}) as hedged:
        race(hedged, "max, hedged 0.8s")


if __name__ == "__main__":
    main()
