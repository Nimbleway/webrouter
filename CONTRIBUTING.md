# Contributing

Thanks for helping out. This project is pre-1.0 and moving fast — open an issue
before starting anything large so we can agree on the shape first.
Questions, or want to talk a change through first? Ask in the [Discord](https://discord.gg/MGztFWSjZh),
in #contributing.

Contributions are accepted under the Apache License 2.0, the same license as the
project ([LICENSE](LICENSE)).

Read [STYLE_GUIDE.md](STYLE_GUIDE.md) before your first PR. It describes the
standards code is reviewed against here, several of which are not obvious and
are enforced by review rather than by tooling.

## Setup

We use [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
git clone https://github.com/Nimbleway/webrouter.git
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

Pre-commit runs the same lint/format/type checks on staged files. CI runs them
again on every pull request, with the unit tests; run `make check` before you push
so a red CI does not cost a review round. After editing `pyproject.toml`, run
`make lock && make requirements` and commit the results.

`requires-python` is `>=3.11` — the floor the package supports. `.python-version` pins 3.12, which is just the interpreter `uv` uses
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

Register a backend at runtime for your own process, or submit it so it ships
in the package. A runtime example is
[`examples/custom_provider.py`](examples/custom_provider.py). Catalogue
submission is the steps below.

| Aspect | Runtime registration | Catalogue submission |
| --- | --- | --- |
| Visibility | Private to your application | Available to every web-router install |
| Installation | You install its dependencies | The method's extra (`search`, `extract`, `research`), `aws` for `agentcore`, `all` for browser sessions and browse |
| Reference | String name only | `Provider` enum + string |
| Configuration | Your choice | Environment variables + `provider_config` |
| Maintenance | Your responsibility | Maintained with web-router |
| Distribution | Your code | Shipped with the package |
| Time to use | Immediate | After merge, on the next `git pull && make setup` |
| Use case | Internal, experimental, custom | Generally available APIs, vendor-supported |

Promoting a shared option on a backend that already exists is
[Promoting a provider option](#promoting-a-provider-option). Wire fields live
in [`contracts/`](contracts/) and are regenerated with `make contracts`.

There is a base class per method, and a provider serves one or both. A vendor
that already serves one gets a second class in its existing module rather than a
new file — `nimble.py`, `tavily.py` and `exa.py` each hold a search provider and
an extract provider.

Whatever the method, finish with `make contracts`, which regenerates the
provider contracts; [AGENTS.md](AGENTS.md#provider-contracts) says how to start one.
Add the backend's list price to `src/web_router/data/prices.json`, from the
vendor's own pricing page (`unknown` if it publishes none), and run
`make prices`; [AGENTS.md](AGENTS.md#provider-prices) has the rules.

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
3. Start with `supported_parameters = {"max_results"}`; each shared option is
   added on its own, where the API documents a field or query operator for it
   (see [Promoting a provider option](#promoting-a-provider-option)). Anything
   outside the set raises `UnsupportedParameterError` rather than being dropped,
   which is the point: a query must never quietly run without a filter the
   caller asked for, and the error names the two ways forward.
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

A provider is called with the options it maps and nothing else; whatever else
its API can do is reachable only through `provider_options`, untranslated and
provider-specific. Mapping one properly makes it work the same way across every
backend, for every user.

The clearest gaps right now: `locale` on Exa and Parallel and `recency` on WebIQ
on the search side (the README support table lists the rest), and on the extract
side nobody maps JS rendering or geo-targeting even though most of those
backends support both.

### Mapping an existing option to a provider (the common case)

`SearchRequest` already has `search_depth`, `include_domains`,
`exclude_domains`, `country`, `locale`, `recency` and `effort`; most search
providers map most of them. To add one:

1. Add the normalized name to that provider's mapping table, in
   `constants_<provider>.py` or, for an older provider, `constants.py` (for
   example `TAVILY_PARAM_NAMES`), pointing at the provider's own field name.
2. On every provider that would be gaining an option, `supported_parameters` is
   derived from that table, so it updates itself; an option the provider
   translates rather than renames is added to the set beside the table.
3. Add a unit test for the mapping, and extend the provider's parametrized
   rejection test so the option is no longer listed as unmapped.
4. Run `make contracts`, and move the option out of the contract's
   `vendor_options` — the test fails while a mapped field is still listed there.
5. Update the README support table and `CHANGELOG.md`.

Watch for options that mean the same thing but take a different form. Tavily's
`country`, for instance, wants a lowercase country name and rejects the ISO
codes the shared surface uses — so mapping it means translating it, not just
renaming it. That translation belongs in the provider. Reuse the shared
helpers rather than writing another: `providers/_clock.recency_start` for a
start date, `providers/_utils.site_operators` for `site:` operators in the
query, and `providers/_language` for a locale's language or a fixed language
list.

A backend that maps an option but cannot serve every value of it refuses the
value on the class, before anything is sent, so `auto` passes it over keyless
and a router's default drops it:

- **`search_depth` levels**: set `depth_levels` to the levels it serves. Each
  level is a size (the [Search guide](https://docs.webrouter.net/search#search-depth)); one whose
  longest body is short of a page lists no `deep`.
- **Any other value** (a country off its list, both domain lists at once):
  override the classmethod `refused_options` to return each refused option and
  its value, and give each option a sentence in `refusal_reasons`.
  `perplexity.py` and `parallel_search.py` are the examples.

A field the vendor documents but its SDK's signature lacks, and that the SDK
still forwards into the body from `**kwargs`, goes in `SDK_FORWARDED` in
`tests/test_contract.py`, with the SDK source that shows it.

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
- Open the PR as a draft. Mark it ready once Qodo's review covers the latest commit and each
  finding has a reply starting with `@qodo` (`Fixed in <sha>: <what>` or
  `Not fixing: <reason>`); the maintainers are asked to review only then. Qodo does not review
  drafts on its own; a `/agentic_review` comment asks it to.
- Don't commit drafts, decks, PDFs or images for feedback; open a
  [Discussion](https://github.com/Nimbleway/webrouter/discussions) and attach them.

Every unit test must work without an API key. Anything that needs one belongs
behind `@pytest.mark.integration`.

## Releases

The maintainers cut each release. A pull request merged here is carried into
the next one.

A release is a `vX.Y.Z` tag on `main` whose `_version.py` says `X.Y.Z`. A
maintainer runs the [Release](.github/workflows/release.yml) workflow on that
tag (`gh workflow run release.yml --ref vX.Y.Z -f tag=vX.Y.Z`): it builds the sdist and wheel from the tag, and after a maintainer
approves the `pypi` environment, publishes them to
[PyPI](https://pypi.org/project/webrouter/) through trusted publishing, so no
upload token exists. It refuses a tag that is not on `main`, does not match
`__version__`, or is not above every version already on PyPI.

## AI coding agents

[AGENTS.md](AGENTS.md) holds the conventions agents should follow in this repo.
Keep it in sync when the ones above change.
