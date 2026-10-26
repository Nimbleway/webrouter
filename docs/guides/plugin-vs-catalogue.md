# Plugin vs Catalogue: Runtime Registration vs Built-in Providers

web-router supports two ways to add providers: **runtime registration** for your own use, and **catalogue submission** for upstream inclusion.

## Runtime registration (plugin)

Use `register_provider()` (search), `register_extract_provider()`, `register_template_provider()`, `register_research_provider()`, `register_browser_provider()` or `register_browse_provider()` to add a provider at runtime without modifying the web-router package itself. One function per method. Each has an `unregister_*` counterpart. A registered name overrides a built-in of the same name.

```python
from typing import Any, ClassVar

from web_router import WebRouter, register_provider
from web_router.providers import HttpSearchProvider
from web_router.types import SearchRequest, SearchResponse, SearchResult


class AcmeProvider(HttpSearchProvider):
    name: ClassVar[str] = "acme"
    supported_parameters: ClassVar[frozenset[str]] = frozenset({"max_results"})
    base_url: ClassVar[str] = "https://api.acme.example"
    search_path: ClassVar[str] = "/v1/search"
    env_api_key: ClassVar[str | None] = "ACME_API_KEY"

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {"q": request.query}
        if request.max_results is not None:
            payload["limit"] = request.max_results
        payload.update(request.provider_options)  # the caller's escape hatch wins
        return payload

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[SearchResult(url=hit["url"], title=hit.get("title") or "") for hit in payload.get("hits", [])],
            raw=payload,
        )


register_provider("acme", AcmeProvider)

router = WebRouter("acme")
response = router.search("query")
```

### When to use runtime registration

- **Private/internal APIs**: your organization's search service
- **Experimental providers**: testing a new integration before submitting upstream
- **Custom wrappers**: adapting a provider to your specific needs
- **Rapid prototyping**: trying out an API without going through the PR process

### Characteristics

- **Registered at runtime**: `register_provider()` call in your application code
- **Not included in extras**: no dependency management; you install SDKs separately
- **Named by string**: `WebRouter("acme")`, not `WebRouter(Provider.ACME)` (the enum is closed)
- **Available immediately**: no waiting for releases or package updates
- **Scoped to your application**: does not affect other users

See `examples/custom_provider.py` for a complete working example (Wikipedia's open search API, no key needed).

## Catalogue submission (built-in)

Submit a PR to add your provider to the built-in catalogue. Once merged, it becomes available to all web-router users.

### When to submit to the catalogue

- **Generally available APIs**: services anyone can sign up for
- **Vendor-maintained**: you work on the API and want to ensure proper integration
- **Widely useful**: the provider serves a common use case
- **Production-ready**: tested, documented, and ready for others to use

### Requirements

All the items from [Adding a provider](../../CONTRIBUTING.md#adding-a-provider), plus:

- **Live verification**: mapping must be confirmed against the real API, not just documentation
- **Integration tests**: at least one `@pytest.mark.integration` test (skips without credentials)
- **Provider enum entry**: added to `Provider` in `types.py` so users can reference it as `Provider.ACME`
- **Documentation**: row in README support table, entry in CHANGELOG
- **Example compatibility**: runnable examples should work with your provider where applicable
- **Provider contract**: `contracts/<method>/<provider>.json`, regenerated with `make contracts`

### Process

1. Open an issue describing the provider you want to add
2. Wait for maintainer feedback (ensures the provider is a good fit)
3. Follow [Adding a provider](../../CONTRIBUTING.md#adding-a-provider), then run `make contracts`
4. Submit a PR with live evidence in the description
5. Address review feedback
6. Once merged, your provider reaches every clone on its next `git pull && make setup`

### Characteristics

- **Registered in `registry.py`**: part of the package source
- **Included in extras**: the method's extra brings it in (`uv sync --extra search` in a clone, or `make setup` for every extra)
- **Named by enum**: `WebRouter(Provider.ACME)` (strings still work)
- **Configured via environment**: credential variables in the `*_ENV_BY_PROVIDER` tables in `constants.py`, the README and `.env.example`, so `configured_providers()` reports it
- **Can join `auto`**: appended after the keyless tier, never ahead of existing members without a scan. DeepSeek is the latest search member added that way (`AUTO_APPENDED`). See [Adding a provider](../../CONTRIBUTING.md#adding-a-provider).
- **Available to every install**: anyone installing web-router can use it
- **Maintained upstream**: receives updates and bug fixes with the package

## Comparison

| Aspect | Runtime Registration | Catalogue Submission |
| --- | --- | --- |
| Visibility | Private to your application | Available to every web-router install |
| Installation | Manual (you handle dependencies) | Automatic (the method's extra, e.g. `uv sync --extra search`) |
| Reference | String name only | `Provider` enum + string |
| Configuration | Your choice | Environment variables + `provider_config` |
| Maintenance | Your responsibility | Maintained with web-router |
| Distribution | Your code | Shipped with the package |
| Time to use | Immediate | After merge + release |
| Use case | Internal, experimental, custom | Generally available APIs, vendor-supported |

## Migration path

Start with runtime registration for fast iteration, then submit to the catalogue when ready:

1. **Prototype**: use `register_provider()` to test the integration
2. **Validate**: confirm the mapping works correctly against the live API
3. **Refine**: adjust parameter mappings, error handling, response normalization
4. **Test**: add unit tests and integration tests
5. **Submit**: open a PR following [Adding a provider](../../CONTRIBUTING.md#adding-a-provider)

## See also

- [Adding a provider](../../CONTRIBUTING.md#adding-a-provider): catalogue submission
- [Promoting a provider option](../../CONTRIBUTING.md#promoting-a-provider-option): mapping shared parameters to existing providers
- [Provider contracts](../../contracts/): `make contracts` regenerates `contracts/<method>/<provider>.json`
- [`examples/custom_provider.py`](../../examples/custom_provider.py): runtime registration example
