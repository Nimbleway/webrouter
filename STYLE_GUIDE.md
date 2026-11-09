# Style Guide

What reviewers look for in this repository. [CONTRIBUTING.md](CONTRIBUTING.md)
covers the mechanics of setting up and opening a PR; this covers the standards
the code itself is held to.

Most formatting is enforced by tooling — run `make check` and it will tell you.
This document exists for the parts a linter cannot judge.

## The one rule everything else serves

**A call must behave the same whichever backend serves it.** That is the entire
value of the package. A change that leaks provider-specific behavior into
callers has failed, even when every test passes.

Ask of any change: *could a user tell which provider answered, other than by
reading `response.provider`?* If yes, and it was not deliberate, fix it.

## Design rules

These are not stylistic preferences. Breaking one is a blocking review comment.

### Never invent data

If a provider returns no relevance score, `score` stays `None`. Do not
synthesize, estimate, default-to-zero, or backfill from another field. A caller
must be able to trust that a populated field came from the provider.

### Never silently drop a parameter

Every provider declares `supported_parameters`. Anything outside that set raises
`UnsupportedParameterError`. A search that quietly ignores `country="US"` is
worse than one that fails, because the caller ships it and never finds out.

When a provider gains real support for a parameter, add it to that set. Never
special-case it at the call site.

### Never substitute one output format for another

An extract provider declares `supported_formats`, and a format outside that set
raises `UnsupportedFormatError`. Returning HTML to a caller who asked for
markdown is the same failure as ignoring `country`, one layer down: the code
that pipes `content` into a markdown parser has no way to notice.

`ExtractedDocument.format` must always name what `content` actually holds. This
is why providers send the format explicitly even when it is the API's own
default — see the first exception below.

### Unset means unset

Request fields left as `None` must not reach the provider, so each API applies
its own defaults rather than inheriting ours. Use `_utils.compact()`, which
`build_payload()` already applies for you.

There are three deliberate exceptions, and each is worth understanding before
adding a fourth. The first: an extract provider resolves `format` even when the caller
named none, and sends it wherever its API has a field for it. It fills in
`EXTRACT_DEFAULT_FORMAT` — markdown — wherever the provider can return it, and
the provider's own `default_format` only where it cannot (Exa's text). This does
change the call for a backend whose own default is HTML (Nimble, Oxylabs, Bright
Data), and deliberately: the same call must not hand an agent raw HTML from one
backend and markdown from another. The label on the result is then true because
it was asked for, rather than true because a vendor default has not moved since
we last read the docs. `ExtractProvider.format_of` carries the same note, and
`auto` reads the same constant (`AUTO_EXTRACT_DEFAULT_FORMAT`).

The second: a browser session opened with no `timeout` is sent
`BROWSER_TIMEOUT`, ten minutes, rather than the vendor's own limit. Unlike
`format` this does change the call, and deliberately: a session is billed until
something ends it, some backends' default is hours, and a caller who forgot to
release one should lose ten minutes of credit rather than an afternoon's.
`BrowserProvider` applies it after validation and only to a backend that maps
`timeout`, so a backend with no lifetime limit is not made to refuse an option
nobody set. `BROWSER_TIMEOUT` carries the same note.

The third, and the narrowest: `local` launches `--headless=new` when `headless`
is unset. There is no vendor default to keep — the binary's own default is a
window — and one nobody asked for would open on the caller's screen.

### Normalization is never lossy

Unmapped provider fields go in `SearchResult.extra` or
`ExtractedDocument.extra`; the untouched payloads go in `SearchResponse.raw` and
`ExtractResponse.raw`. A user must always be able to recover what the provider
actually said.

### A partial failure stays partial, and a total one raises

Extracting several URLs can partly succeed. What came back goes in
`documents`, every URL that did not goes in `failures`, and a call where nothing
came back raises — so a bad credential can never read as a page with no text.

`ExtractProvider` enforces this for every backend, including the fan-out that
makes a one-URL-per-call API behave like a batching one. Do not reimplement any
of it in a provider.

### Wrap every provider failure

SDK and HTTP exceptions become `ProviderError`, with the original preserved on
`__cause__`. Callers should never need to import a provider's SDK to catch its
errors.

The base class does this for you: implement `_search` and `_extract`, never
`search` or `extract`. If an invariant can be enforced in the base class instead
of asked for in review, put it there — a rule a new provider cannot break is
worth more than a rule written down.

### Verify against the live API, not the documentation

Vendor docs have been wrong here, and so have vendor SDKs. Cases found by
calling the API rather than reading about it, or by reading it closely enough to
distrust — the code records each one where the next person will hit it:

- You.com documents an `extraction_formats` field that the service rejects
  with a 422.
- Tavily's `chunks_per_source` belongs to its extract and crawl endpoints, not
  to search. Sent to search it rode the SDK's `**kwargs` and was dropped, which
  made `search_depth="fast"` an exact duplicate of `"lite"` — a payload that
  looked different and behaved identically.
- Tavily's extract endpoint rejects `format="html"` with a 422, though nothing
  in its SDK signature says so.
- Nimble accepts `search_depth` values its own validator does not list and its
  SDK types as invalid. They work today, as undocumented aliases; mapping onto
  them would have broken on the first release that tightened validation.
- Jina Reader puts the page body under a different key for each format —
  `data.content` for markdown, `data.html` for HTML — and omits the others
  entirely, so a single hardcoded key silently returns empty content.
- Bright Data's API reference shows a JSON envelope beside a `format="raw"`
  request. `raw` returns the page as the HTTP body and is not JSON at all;
  reading it as one fails outright. Caught in the reference rather than by a
  call — no account was available, which is exactly why the mapping says so.

Before you trust a parameter, call it. Note anything surprising in a comment,
because the next person will otherwise "fix" it back — and prefer a guard that
makes the mistake impossible to a note asking people not to make it, as
`tests/test_contract.py` and `tests/test_extract_contract.py` do for the ones
that can be caught offline: two mappings that build the same request, and a key
sent to an SDK that does not declare it.

## Send providers the minimum

A provider is called with what the method actually needs — the query and the
shared options it maps (`max_results`, `search_depth`, `include_domains`,
`exclude_domains`, `country`, `locale`, `recency`, `effort`) for a search; the
URLs and `format` for an extract — and nothing else. No option is sent because the API happens to accept
it.

This keeps each integration small enough to verify completely, and keeps the
cost of a wrong mapping near zero. It also leaves the interesting work visible:
the gap between what a provider maps and what its API can do is the contribution
backlog, and it is meant to be filled by the people who know that API best.

Two consequences worth stating plainly:

- **Do not widen a provider's mapping as a side effect of another change.**
  Mapping an option is its own PR with its own live evidence.
- **Do not add machinery for an option nobody mapped.** A 166-entry country
  lookup for a parameter no provider sends is dead weight; it was written and
  removed here once already.

Anything beyond the mapped set is reachable through `provider_options`,
untranslated, and raises `UnsupportedParameterError` if set as a normalized
option — never silently dropped. That error message is the contribution prompt,
so keep it pointing at both routes forward.

See [CONTRIBUTING.md](CONTRIBUTING.md#promoting-a-provider-option).

## Code

- **Python 3.10+.** `from __future__ import annotations` at the top of every
  module. Full type annotations; `mypy --strict` must pass on `src/`.
- **Line length 120**, ruff-formatted. Do not hand-wrap what the formatter owns.
- **No hardcoded literals in logic.** Endpoints, env var names, timeouts, header
  names and message templates live in `constants.py`.
- **Name things for what they mean to a caller**, not for their type.
  `search_depth`, not `depth_enum`.
- **Prefer a table to a branch.** Provider parameter mappings are dicts in
  `constants.py`, not chains of `if`.
- **Private helpers are prefixed with `_`** and may skip docstrings when the
  name and signature already say it. Everything public gets one.

## Comments

Comments explain **why**, never **what**. The code already says what it does.

```python
# Good - explains something the code cannot.
# `full_page` yields markdown on its own -- sending the documented
# `extraction_formats` alongside it is rejected with a 422.

# Bad - restates the line below it.
# Loop over the results and build a SearchResult for each one.
```

Do not leave behind:

- narration of your own process ("first we...", "now we need to...")
- TODOs for work you were asked to do in this PR
- commented-out code — git remembers it
- section banners like `# ---- Helpers ----` inside a class
- docstrings that only repeat the function name

A comment that will be wrong after the next refactor is worse than no comment.

## Tests

- **Unit tests never touch the network.** Register a fake provider; see
  `tests/conftest.py`.
- **Live tests are marked `@pytest.mark.integration`** and must skip themselves
  when the relevant key is absent. `make test` runs with no secrets, so anything
  that needs a key must be excluded by that marker. A keyless provider's live
  test needs no `skipif` — the marker alone keeps it out of `make test`.
- **Test behavior a user depends on, not internal shape.** One test proving a
  mapping is correct beats three that restate the implementation.
- **Name the test after the guarantee**, not the function under test:
  `test_unsupported_parameter_is_rejected_not_dropped`, not `test_validate`.
- **Every bug fix gets a regression test** that fails before the fix.

## Commits and PRs

Conventional-commit subjects, lowercase, imperative:

```
feat: add exa provider
fix: stop dropping country on tavily
docs: clarify search depth mapping
chore: bump ruff to 0.16
```

A pull request should:

- do one thing — a provider, a fix, a refactor; not all three
- pass `make check` before review
- explain *why* in the description, not just what changed
- update `CHANGELOG.md` under `## [Unreleased]` if users would notice
- update the README support table if provider capabilities changed
- include live-call evidence when it touches a provider mapping

Keep PRs small enough to review properly. If a change is large by nature, say so
up front and suggest an order to read the files in.

## When a rule is wrong

These rules exist to be argued with. If one is blocking something genuinely
better, say so in the PR and propose the change to this document in the same
breath. What is not fine is quietly ignoring one.
