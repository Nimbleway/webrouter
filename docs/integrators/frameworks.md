# Framework integration

Integrate once against web-router's methods. Which backend serves a call stays the caller's choice. The [README](../../README.md) is the reference for the methods, credentials, errors, and the shipped LangChain, Claude, OpenAI, and MCP integrations.

`auto` uses every key held. A key held only for research also serves plain searches, and those calls bill. A caller who wants another order passes `provider_config={"auto": {"chain": ...}}`. See [Working with no credentials](../../README.md#working-with-no-credentials).

## Checklist

When integrating web-router:

- [ ] Accept a `search_provider` or `extract_provider` parameter
- [ ] Default to `"auto"` (web-router's default provider)
- [ ] Pass it to `WebRouter(search_provider)` or `AsyncWebRouter(...)`
- [ ] Use `router.search()` or `router.extract()` in your implementation
- [ ] Let `WebRouterError` propagate or wrap it in your own exception
- [ ] Document credential environment variables (`NIMBLE_API_KEY`, etc.)
- [ ] Document that `auto` tries credentialed backends, then keyless ones, and that every key a user holds is used, so a key held only for research also serves (and bills) plain searches. Users who want a different order pass `provider_config={"auto": {"chain": ...}}`.

Error types are in the [README](../../README.md#errors). Adding a backend is [Adding a provider](../../CONTRIBUTING.md#adding-a-provider). A private backend is [Plugin vs catalogue](../guides/plugin-vs-catalogue.md).
