"""Live data: find a source in a vendor's catalogue, read its inputs, run it.

A live data source is a vendor's ready-made scraper for one site -- a
retailer's product page, a job board's listing, a search results page -- that
fetches the site for this call and returns records with fixed fields, rather
than page text to read through. `live_data_sources()` lists the catalogue,
`live_data_source()` says what one takes and returns, and `live_data()` runs
it; the records come back in `response.records`.

Runs against Nimble's Extract Templates. Needs a key, and names the vendor on
every call: a source's name belongs to one vendor, so there is no `auto` and
no anonymous tier. With no credential at all this prints what to set and exits.

Run it with::

    make setup   # in your webrouter clone: every extra
    uv run --env-file .env python examples/live_data.py
"""

from __future__ import annotations

import json

from web_router import ProviderError, WebRouter, configured_live_data_providers

USABLE = [name for name in configured_live_data_providers() if name != "auto"]

if not USABLE:
    raise SystemExit("No live data backend is configured. Set NIMBLE_API_KEY and run this again.")


def catalogue(router: WebRouter) -> str | None:
    """Page through the catalogue and pick the first source that can be run.

    A catalogue can hold thousands, so this reads one page of it rather than
    the whole thing; `total` says how far the rest goes.
    """
    page = router.live_data_sources(provider="nimble", limit=20)
    print(f"{page.provider} holds {page.total} sources; the first {len(page)}:")
    for template in page.sources:
        state = "published" if template.published else "no published version"
        print(f"  - {template.name:<45} {template.domain or '-':<30} {state}")

    runnable = [template for template in page.sources if template.published]
    return runnable[0].name if runnable else None


def describe(router: WebRouter, name: str) -> dict[str, object]:
    """What a run has to pass: the source's own input schema, which differs per source.

    Some take a URL, others an id, a slug or a search term. The sample the
    backend publishes beside the schema, when there is one, is a ready-made
    set of inputs.
    """
    template = router.live_data_source(name, provider="nimble")
    print(f"\n{template.name}: {template.description or '(no description)'}")
    print(f"  inputs:  {json.dumps(template.input_schema)}")
    print(f"  returns: {list((template.output_schema or {}).get('properties', {}))}")

    return dict(template.samples[0].input) if template.samples else {}


def run(router: WebRouter, name: str, inputs: dict[str, object]) -> None:
    """One set of inputs in, the records out. Ask for a `format` to get the page beside them."""
    try:
        response = router.live_data(name, inputs, provider="nimble", format="markdown")
    except ProviderError as exc:
        # A page the backend could not fetch, or a source that failed on it,
        # raises rather than coming back empty.
        print(f"\nthe run failed: {exc}")
        return

    print(f"\n{response.provider} ran {response.source} on {response.url}: {len(response.records)} record(s)")
    print(json.dumps(response.records, indent=2)[:800])
    if response.document is not None:
        print(f"\n...and the page beside it, {len(response.document.content)} characters of {response.document.format}")


def main() -> None:
    with WebRouter() as router:
        name = catalogue(router)
        if name is None:
            print("nothing on this page of the catalogue is published, so there is nothing to run")
            return
        inputs = describe(router, name)
        if not inputs:
            print("the source publishes no sample inputs; fill in its input schema by hand to run it")
            return
        run(router, name, inputs)


if __name__ == "__main__":
    main()
