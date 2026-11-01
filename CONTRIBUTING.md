# Contributing

Thanks for helping out. This project is pre-1.0 and moving fast — open an issue
before starting anything large so we can agree on the shape first.

Read [STYLE_GUIDE.md](STYLE_GUIDE.md) before your first PR. It describes the
standards code is reviewed against here, several of which are not obvious and
are enforced by review rather than by tooling.

## Setup

We use [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
git clone https://github.com/WebContextLayer/webrouter.git
cd webrouter
make install
```

That creates `.venv`, installs every extra plus the dev group, and installs the
pre-commit hooks.

## Day-to-day

```bash
make format           # ruff format + autofix
make lint             # ruff, no fixes
make typecheck
make test             # unit tests, offline
make check            # lint + typecheck + unit tests
make test-integration # tests that hit real provider APIs (needs credentials)
make test-browser     # tests that launch a real local Chromium
```

Pre-commit runs the same lint/format/type checks on staged files. This
repository has no CI, so `make check` on your machine is the gate a reviewer
relies on. After editing `pyproject.toml`, run `make lock && make requirements`
and commit the results.

`requires-python` is `>=3.10` — the floor the package supports. `.python-version` pins 3.12, which is just the interpreter `uv` uses
for local development, not a requirement.

## Tests

- Unit tests must not touch the network. Register a fake provider (see
  `tests/conftest.py`) instead of calling a real API.
- Tests that do hit a live API are marked `@pytest.mark.integration` and skip
  themselves when the relevant credential is absent. Put your keys in `.env`
  (copy `.env.example`); it is loaded automatically and is gitignored. Run them
  with `make test-integration`.
- Tests that launch a real local browser are marked `@pytest.mark.browser`,
  skip themselves when no Chromium is installed, and are excluded from
  `make test`. Run them with `make test-browser`.
- New provider? Add a `tests/providers/test_<provider>.py` covering parameter
  mapping and response normalization against a recorded payload.

## Adding a provider

There is a base class per method, and a provider serves one or both. A vendor
that already serves one gets a second class in its existing module rather than a
new file — `nimble.py`, `tavily.py` and `exa.py` each hold a search provider and
an extract provider.

Whatever the method, finish with `make contracts`, which regenerates the
provider contracts; [AGENTS.md](AGENTS.md#provider-contracts) says how to start one.

### A search provider

1. Create `src/web_router/providers/<name>.py` with a `SearchProvider` subclass
   setting `name` and `supported_parameters`. You implement the underscored
   methods; the base class wraps them with validation and error handling, so
   never override `search`/`asearch` themselves.

   **If the API has no Python SDK — or you only need a couple of its fields —
   subclass `HttpSearchProvider` instead** (`providers/_http.py`). It owns
   client construction, auth headers, HTTP error mapping and cleanup, leaving
   you two methods: `_build_payload` and `_to_response`. Brave, Exa, Parallel,
   Perplexity and You.com are all built this way and are the shortest examples
   to copy. Subclass `SearchProvider` directly only when wrapping a real SDK, as
   Nimble and Tavily do.

   `HttpSearchProvider` clients never follow redirects, and a provider sending
   a credential refuses to. `httpx` replays the auth header to the redirect
   target even on another host, so if your endpoint redirects, point `base_url`
   at where it lands rather than turning redirects on.

   An endpoint that needs no credential sets `env_api_key = None`: no auth
   header is sent and a missing key never raises. `you_mcp.py` is the worked
   example, and it also shows the other case worth knowing — when a second
   endpoint returns the same payload shape as an existing provider, subclass
   that provider and override only the transport rather than copying its
   normalization.
2. Map the provider's payload onto `SearchResponse`; keep anything unmapped in
   `SearchResult.extra` and `SearchResponse.raw`. Don't invent values the
   provider didn't return — no synthesized scores or dates.
3. Start with `supported_parameters = {"max_results"}`, plus `search_depth` if
   the API has a content-depth control — Brave, Exa, Parallel and Perplexity
   have none. That is the minimum a search needs; send the provider nothing
   else. Anything outside the set raises `UnsupportedParameterError` rather than
   being dropped, which is the point: a query must never quietly run without a filter the caller asked
   for, and the error names the two ways forward.
4. Wrap SDK failures in `ProviderError` so callers can handle every backend the
   same way.
5. Add the SDK to the extra for each method the provider serves in
   `pyproject.toml` — extras are named per method (`search`, `extract`), never
   per vendor, so a search backend goes in `search` rather than getting one of
   its own. The one exception is `aws`: a backend reached through an AWS
   account signs with botocore, too large to impose on every `search` install.
   Register the module in
   `_BUILTIN_PROVIDERS` in `src/web_router/registry.py`, and add an entry to
   `ENV_BY_PROVIDER` in `constants.py` naming the variable its credential comes
   from — or `None` if it needs none. `configured_providers()` reads that
   table, and `tests/test_contract.py` fails if a provider is missing from it.
6. Add tests — parameter mapping and response normalization against a recorded
   payload, plus one `@pytest.mark.integration` live test — and a row in the
   README support table.

### An extract provider

The same shape, one method along. `firecrawl.py` is the shortest complete
example.

1. Subclass `HttpExtractProvider` and implement `_build_payload` and
   `_to_response`, or `ExtractProvider` and `_extract`/`_aextract` when you are
   wrapping an SDK. Never override `extract`/`aextract`: the base class runs
   validation, wraps failures, and fans the URLs out across calls for you.
2. Put your format mapping in `constants_<provider>.py` as a table keyed by
   `ExtractFormat`, and derive `supported_formats = frozenset(ACME_EXTRACT_FORMATS)`
   from it so the table stays the single description of what the provider can
   do. Set `default_format` to what the API returns when asked for nothing in
   particular — check this against a live call, because it is what
   `ExtractedDocument.format` reports on every call that named no format. Read
   the format through `format_of()` and send it wherever the API has a field
   for it; if the API expresses its default by omission, as Oxylabs' and Bright
   Data's do, send nothing for that one and the label is still right.
3. Set `batches_urls = True` only if one call really takes every URL, as
   Tavily's and Exa's do -- `auto` also sets it, for the different reason that
   it delegates the whole request to a member. Leave it alone and the base class
   calls you once per URL and assembles one response, which is what most of
   these APIs need.
4. Map the payload onto `ExtractResponse`. If the API answers 200 and reports a
   refused fetch in a status field — Nimble and Firecrawl both do — return that
   URL as an `ExtractFailure` rather than a document with empty content. A
   non-200 status for the *page* is not a failure; it belongs in
   `ExtractedDocument.status_code`.
5. Register the module in `_BUILTIN_EXTRACT_PROVIDERS` in `registry.py`, add an
   entry to `EXTRACT_ENV_BY_PROVIDER` in `constants.py` — a tuple of every
   variable the provider needs, `()` if it needs none — and add each variable to
   `.env.example`.
6. Add `tests/providers/test_<name>.py` and the live checks in
   `tests/test_integration_surface.py`, which run only against providers
   declaring the format under test. Add a row to the README's extract support
   table.

If your auth scheme is not a single header, override `_auth_headers` rather than
building the header yourself — Oxylabs' HTTP Basic is the worked example. Doing
it that way keeps `api_key` set, which is what the redirect refusal above keys
off; a provider that hides its credential from the base class would quietly opt
out of that protection.

Check provider behavior against the live API, not just its documentation. We
have already been bitten several times: You.com documents an
`extraction_formats` field that the service rejects with a 422, Tavily rejects
`format="html"` on extract, Jina Reader moves the page body to a different key
for each format, and Bright Data's own reference shows a JSON envelope beside a
`format="raw"` request that does not return one.

Where no account is available, say so in the test module's docstring the way
`tests/providers/test_oxylabs.py`, `test_brightdata.py` and `test_firecrawl.py`
do, so the next person knows the mapping is documentation-derived rather than
verified.

### A research, answer, browser, or browse provider

The same shape again. The step-by-step recipes live in
[AGENTS.md](AGENTS.md)'s "Adding a research provider", "Adding an answer
provider", "Adding a browser provider" and "Adding a browse provider" sections — written for coding agents,
but they apply to humans exactly the same.

## Dependencies

Edit `pyproject.toml`, then:

```bash
make lock
make requirements   # regenerates requirements*.txt from uv.lock
```

Commit `uv.lock` and the regenerated requirements files together. Never edit
`requirements.txt` by hand — it is generated.

## Promoting a provider option

**This is the contribution we most want**, and the fastest way to make
web-router represent your API properly.

Each provider is deliberately called with the minimum the method needs — the
query, `max_results` and `search_depth` for a search; the URLs and `format` for
an extract. Their APIs can all do far more, and today those extras are only
reachable through `provider_options`, untranslated and provider-specific.
Mapping one properly makes it work the same way across every backend, for every
user.

The clearest gaps right now: `country` and the domain filters are mapped by
Nimble alone on the search side, and on the extract side nobody maps JS
rendering or geo-targeting even though most of those backends support both.

### Mapping an existing option to a provider (the common case)

`SearchRequest` already has `include_domains`, `exclude_domains`, `country` and
`locale`. Nimble maps them; no other provider does yet.
To add one:

1. Add the normalized name to that provider's mapping table, in
   `constants_<provider>.py` or, for an older provider, `constants.py` (for
   example `TAVILY_PARAM_NAMES`), pointing at the provider's own field name.
2. On every provider that would be gaining an option, `supported_parameters` is
   derived from that table, so it updates itself. (Nimble declares the whole
   normalized set directly, because it already maps all of it.)
3. Add a unit test for the mapping, and extend the provider's parametrized
   rejection test so the option is no longer listed as unmapped.
4. Run `make contracts`, and move the option out of the contract's
   `vendor_options` — the test fails while a mapped field is still listed there.
5. Update the README support table and `CHANGELOG.md`.

Watch for options that mean the same thing but take a different form. Tavily's
`country`, for instance, wants a lowercase country name and rejects the ISO
codes the shared surface uses — so mapping it means translating it, not just
renaming it. That translation belongs in the provider.

### Adding a new option to the shared surface

Only if it means the same thing on at least two backends — otherwise it belongs
in `provider_options`. Open an issue first if you are unsure.

1. Add the field to `SearchRequest` in `types.py`. `NORMALIZED_PARAMETERS` is
   derived from the model, so it updates itself.
2. Add the matching keyword to `WebRouter.search` and `AsyncWebRouter.search`
   in `client.py`, and to `_BaseRouter._build_request`. `tests/test_contract.py`
   fails if you miss one.
3. Map it for each provider that supports it, as above.
4. Leave it unmapped elsewhere; those providers will raise, which is correct.
5. Add a live check to `tests/test_integration_surface.py`.

### The one hard requirement

**Show that the mapping is correct with a live call, and put the evidence in the
PR description.** An option in the shared surface is a promise it behaves the
same everywhere, and that is only worth promising one verified option at a
time.

## Commits and pull requests

- Conventional-commit style subjects (`feat:`, `fix:`, `docs:`, `chore:`).
- One logical change per PR, with tests.
- `make check` passes before you ask for review; if you touched
  `pyproject.toml`, `make lock && make requirements` too.
- Note user-visible changes under `## [Unreleased]` in `CHANGELOG.md`. A check fails the PR
  without one; a PR with nothing to note (CI, docs, tests, refactors) takes the
  `skip-changelog` label instead (release bumps too). New lines must sit under
  `## [Unreleased]`, not under a released version.
- Direct commits to `main` are blocked; work on a branch and open a PR.

Every unit test must work without an API key. Anything that needs one belongs
behind `@pytest.mark.integration`.

## Releases

The maintainers cut each release. A pull request merged here is carried into
the next one.

## AI coding agents

[AGENTS.md](AGENTS.md) holds the conventions agents should follow in this repo.
Keep it in sync when the ones above change.
