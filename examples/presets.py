"""Presets: saved settings a call names instead of repeating them.

Loads the two definitions in ``examples/presets/``, applies one to the router
and one to a call and shows what each response says about them. It then shows a
locked setting refusing an override before anything is sent.

Needs no credentials: search and extract fall back to the keyless tiers. YAML
needs PyYAML, which ``make setup`` installs; JSON needs nothing.

Run it with::

    uv run python examples/presets.py
"""

from __future__ import annotations

from pathlib import Path

from web_router import PresetValidationError, WebRouter

PRESETS = Path(__file__).parent / "presets"


def main() -> None:
    # The router's own preset applies to every call it lists; it can be loaded after.
    with WebRouter(preset="acme.supplier-research@1.0.0") as router:
        for preset in router.presets.load(PRESETS):
            print(f"loaded {preset.ref}: {preset.title} ({', '.join(preset.applies_to)})")

        results = router.search("warehouse robotics suppliers")
        meta = results.meta.preset if results.meta else None
        print(f"\nsearch: {len(results)} results from {results.provider}")
        print(f"  presets: {[entry.ref for entry in meta.resolved] if meta else None}")
        print(f"  applied: {meta.effective if meta else None}")

        # An argument the call passes wins over a preset that lets it be overridden.
        fewer = router.search("warehouse robotics suppliers", max_results=2)
        overridden = fewer.meta.preset.overridden if fewer.meta and fewer.meta.preset else []
        print(f"\nmax_results=2 passed: {len(fewer)} results, overridden {overridden}")

        if results.urls:
            page = router.extract(results.urls[0])
            print(f"\nextract: {page.documents[0].format if page.documents else None} from {page.provider}")

        # A per-call preset sits above the router's; this one locks its settings.
        check = router.presets.validate("search", {"query": "q", "max_results": 10}, preset="acme.news@1.0.0")
        print(f"\nvalidate before calling: ok={check.ok}")
        for issue in check.issues:
            print(f"  {issue.field}: {issue.message}")
        try:
            router.search("q", max_results=10, preset="acme.news@1.0.0")
        except PresetValidationError as exc:
            print(f"refused before anything was sent: {exc}")


if __name__ == "__main__":
    main()
