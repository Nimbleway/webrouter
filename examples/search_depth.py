"""`search_depth` across backends: three levels that mean the same thing everywhere.

Providers describe content richness in incompatible vocabularies -- Nimble says
`lite`/`standard`, Tavily says `basic`/`advanced`, You.com says `highlights`/
`full_page`. web-router reduces that to `lite` / `fast` / `deep` and maps each
onto the backend's own knobs.

This prints how much text each level actually returns, which is the only honest
way to see that the mapping does something. Runs with no credentials.

Run it with::

    make setup   # in your web-router-alpha clone: every extra
    python examples/search_depth.py "what is retrieval augmented generation"
"""

from __future__ import annotations

import sys
from statistics import mean

from web_router import UnsupportedParameterError, WebRouter, configured_providers
from web_router.registry import get_provider_class
from web_router.types import SearchDepth

DEPTHS: tuple[SearchDepth, ...] = ("lite", "fast", "deep")


def maps_depth(name: str) -> bool:
    """Brave, Exa, Parallel and Perplexity have no content-depth control at all."""
    return "search_depth" in get_provider_class(name).supported_parameters


def measure(router: WebRouter, query: str, depth: SearchDepth) -> str:
    """Average characters of `content` per result at this depth."""
    try:
        response = router.search(query, max_results=3, search_depth=depth)
    except UnsupportedParameterError:
        return "not mapped"
    # A throttled backend must not empty the whole table.
    except Exception as exc:
        return f"failed ({type(exc).__name__})"

    if not response.results:
        return "no results"
    return f"{round(mean(len(result.content) for result in response.results)):>7,} chars"


def main() -> None:
    query = " ".join(sys.argv[1:]) or "what is retrieval augmented generation"

    usable = [name for name in configured_providers() if name != "auto" and maps_depth(name)]
    if not usable:
        raise SystemExit("no usable provider maps search_depth")

    print(f'query: "{query}"\n')
    print(f"{'provider':<12} " + " ".join(f"{depth:>14}" for depth in DEPTHS))
    print("-" * 60)

    for name in usable:
        with WebRouter(name) as router:
            cells = [measure(router, query, depth) for depth in DEPTHS]
        print(f"{name:<12} " + " ".join(f"{cell:>14}" for cell in cells))

    print("\n`result.content` always holds the fullest text available,")
    print("whichever field the backend happened to put it in.")


if __name__ == "__main__":
    main()
