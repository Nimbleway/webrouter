# Promoting a Provider Option

**This is the contribution we most want**, and the fastest way to make web-router represent your API properly.

Each provider is deliberately called with the minimum the method needs — the query, `max_results` and `search_depth` for a search; the URLs and `format` for an extract. Their APIs can all do far more, and today those extras are only reachable through `provider_options`, untranslated and provider-specific. Mapping one properly makes it work the same way across every backend, for every user.

The clearest gaps right now: `country` is mapped by Nimble alone; `include_domains` and `exclude_domains` are mapped by Nimble, Linkup and DeepSeek, and `locale` by Nimble and DeepSeek. On the extract side nobody maps JS rendering or geo-targeting even though most of those backends support both.

## Mapping an existing option to a provider

The common case. `SearchRequest` already has `include_domains`, `exclude_domains`, `country` and `locale`. Nimble, Linkup and DeepSeek map `include_domains` and `exclude_domains`; Nimble maps `country` and `locale`, and DeepSeek maps `locale`.

To add one:

1. Add the normalized name to that provider's mapping table in `constants.py` (for example `TAVILY_PARAM_NAMES`), pointing at the provider's own field name.
2. On every provider that would be gaining an option, `supported_parameters` is derived from that table, so it updates itself. (Nimble declares the whole normalized set directly, because it already maps all of it.)
3. Add a unit test for the mapping, and extend the provider's parametrized rejection test so the option is no longer listed as unmapped.
4. Update the README support table and `CHANGELOG.md`.

Search `auto` accepts every option some member maps and routes each request only to members that map what it sets, so mapping an option on one more backend widens where `auto` can send those requests. `tests/test_auto.py` checks that `AUTO_SUPPORTED_PARAMETERS` is still the union.

Watch for options that mean the same thing but take a different form. Tavily's `country`, for instance, wants a lowercase country name and rejects the ISO codes the shared surface uses — so mapping it means translating it, not just renaming it. That translation belongs in the provider's `_build_payload`, done after `build_payload()` assembles the base request, so the mapping table names the provider's field and the provider translates the value before sending it.

## Adding a new option to the shared surface

Only if it means the same thing on at least two backends — otherwise it belongs in `provider_options`. Open an issue first if you are unsure.

1. Add the field to `SearchRequest` in `types.py`. `NORMALIZED_PARAMETERS` is derived from the model, so it updates itself.
2. Add the matching keyword to `WebRouter.search` and `AsyncWebRouter.search` in `client.py`, and to `_BaseRouter._build_request`. `tests/test_contract.py` fails if you miss one.
3. Map it for each provider that supports it, as above.
4. Leave it unmapped elsewhere; those providers will raise, which is correct.
5. Add a live check to `tests/test_integration_surface.py`.

## The one hard requirement

**Show that the mapping is correct with a live call, and put the evidence in the PR description.** An option in the shared surface is a promise it behaves the same everywhere, and that is only worth promising one verified option at a time.

## Examples

### Mapping an existing option

Linkup's table on main already maps the domain filters. Mapping `include_domains` for Exa would look like this:

```python
# In constants.py
EXA_PARAM_NAMES = {
    "max_results": "numResults",
    "include_domains": "includeDomains",  # the new mapping
}
```

Note: The table shows the plain rename from web-router's field to the provider's API name. If the provider additionally requires value transformation (e.g., different units or codes), handle that in the provider's `_build_payload` method.

### Adding a new shared option

```python
# In types.py
class SearchRequest(NormalizedRequest):
    ...
    locale: str | None = Field(default=None, description="Language code, e.g. 'en'.")
    start_date: str | None = Field(default=None, description="Only results published on or after this date.")  # new


# In client.py, on WebRouter.search and AsyncWebRouter.search
def search(
    self,
    query: str,
    *,
    provider: str | Provider | SearchProvider | None = None,
    max_results: int | None = None,
    search_depth: SearchDepth | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    country: str | None = None,
    locale: str | None = None,
    start_date: str | None = None,  # new
    provider_options: dict[str, Any] | None = None,
) -> SearchResponse: ...


# ...and in _BaseRouter._build_request. If any auto member maps it, add it to
# AUTO_SUPPORTED_PARAMETERS in constants.py as well.
```

## Checklist

Before submitting an option-promotion PR:

- [ ] Option mapped in provider's table in `constants.py` (for existing options) OR added to `types.py` (for new shared options)
- [ ] Provider's `supported_parameters` includes the option (derived automatically from the mapping table)
- [ ] Unit test covers the parameter mapping
- [ ] Provider's rejection test updated so the option is no longer listed as unmapped
- [ ] Live integration test demonstrates the mapping works correctly
- [ ] `AUTO_SUPPORTED_PARAMETERS` still the union of what `auto`'s members map (new shared options only)
- [ ] README support table updated to show which providers map the option
- [ ] Entry added to `CHANGELOG.md` under `## [Unreleased]`
- [ ] PR description includes live evidence (API call logs/screenshots showing the parameter being sent and working)

## See also

- [Adding a provider](adding-a-provider.md) — full provider integration guide
- [Contract reference](../contract/README.md) — the normalized request/response models
- `tests/test_integration_surface.py` — cross-provider live checks
