"""Extract Templates: find one in the backend's catalogue, read its inputs, run it.

A template is a saved, site-specific extraction -- a retailer's product page, a
job board's listing, a search results page -- that turns one page into a record
with fixed fields, rather than page text to read through. `templates()` lists
the catalogue, `template()` says what one takes and returns, and
`run_template()` runs it; the record comes back in `response.data`.

Needs a key. A template run fetches and parses on the backend's account, so
unlike `search` and `extract` there is no anonymous tier to fall back on: with
no credential at all this prints what to set and exits.

Run it with::

    make setup   # in your webrouter clone: every extra
    uv run --env-file .env python examples/extract_template.py
"""

from __future__ import annotations

import json

from web_router import ProviderError, WebRouter, configured_template_providers

USABLE = [name for name in configured_template_providers() if name != "auto"]

if not USABLE:
    raise SystemExit("No Extract Template backend is configured. Set NIMBLE_API_KEY and run this again.")


def catalogue(router: WebRouter) -> str | None:
    """Page through the catalogue and pick the first template that can be run.

    A catalogue can hold thousands, so this reads one page of it rather than
    the whole thing; `total` says how far the rest goes.
    """
    page = router.templates(limit=20)
    print(f"{page.provider} holds {page.total} templates; the first {len(page)}:")
    for template in page.templates:
        state = "published" if template.published else "no published version"
        print(f"  - {template.name:<45} {template.domain or '-':<30} {state}")

    runnable = [template for template in page.templates if template.published]
    return runnable[0].name if runnable else None


def describe(router: WebRouter, name: str) -> dict[str, object]:
    """What a run has to pass: the template's own input schema, which differs per template.

    Some take a URL, others an id, a slug or a search term. The sample the
    backend publishes beside the schema, when there is one, is a ready-made
    set of inputs.
    """
    template = router.template(name)
    print(f"\n{template.name}: {template.description or '(no description)'}")
    print(f"  inputs:  {json.dumps(template.input_schema)}")
    print(f"  returns: {list((template.output_schema or {}).get('properties', {}))}")

    samples = template.extra.get("samples") or []
    sample_input = samples[0].get("input") if samples else None
    return dict(sample_input) if isinstance(sample_input, dict) else {}


def run(router: WebRouter, name: str, params: dict[str, object]) -> None:
    """One page in, one record out. Ask for a `format` to get the page beside it."""
    try:
        response = router.run_template(name, params, format="markdown")
    except ProviderError as exc:
        # A page the backend could not fetch, or a template that failed on it,
        # raises rather than coming back as an empty record.
        print(f"\nthe run failed: {exc}")
        return

    print(f"\n{response.provider} ran {response.template} on {response.url}")
    print(json.dumps(response.data, indent=2)[:800])
    if response.document is not None:
        print(f"\n...and the page beside it, {len(response.document.content)} characters of {response.document.format}")


def main() -> None:
    with WebRouter() as router:
        name = catalogue(router)
        if name is None:
            print("nothing on this page of the catalogue is published, so there is nothing to run")
            return
        params = describe(router, name)
        if not params:
            print("the template publishes no sample inputs; fill in its input schema by hand to run it")
            return
        run(router, name, params)


if __name__ == "__main__":
    main()
