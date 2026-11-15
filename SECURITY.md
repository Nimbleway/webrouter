# Security Policy

## Reporting a vulnerability

Please do **not** open a public GitHub issue for security problems.

Report it privately through GitHub:
[open a security advisory](https://github.com/Nimbleway/webrouter/security/advisories/new)
with a description of the issue, the affected version, and steps to reproduce.
If you can't use GitHub, email <support@nimbleway.com> instead.
We aim to acknowledge reports within three business days and to keep you
updated until the issue is resolved.

## Supported versions

While the project is pre-1.0, only the latest release receives security fixes.

## Credentials

web-router never logs or persists API keys. Keys are read from the environment
(for example `NIMBLE_API_KEY`) or passed explicitly through `provider_config`
at construction. Nimble's and Tavily's are handed straight to their SDKs; for
every other backend, which web-router calls over HTTP itself, the key goes into
the request header that API expects — Oxylabs' username and password are
combined into an HTTP Basic header, and nothing else transforms a credential.

Every provider's `__repr__` deliberately omits the key and its options, so a
stray log cannot leak one. It is defined once on the base that `SearchProvider`
and `ExtractProvider` share, so search and extract backends alike are covered,
and a contract test asserts it for each of them.

Keep your `.env` out of version control — the shipped `.gitignore` already
excludes it.

The library itself never reads a `.env`. The MCP server does, at startup —
from `WEB_ROUTER_ENV_FILE`, its working directory or your user config
directory — and at runtime through its `load_env` tool, which takes a file's
path from the model. Either way it takes only the provider credential
variables web-router names and ignores everything else in the file, a variable
already set keeps its value, `${...}` is not expanded, and neither the tool's
result, its errors nor the capabilities resource carries a value: names and
paths only. `load_env(remember=true)` records the path, never a copy of the
keys. What this does not prevent: a model steered by a page it read could call
`load_env` on a file someone planted on the machine, adding a key for a backend
you hold none for, and later calls through `auto` could then run on that
account. See [the MCP guide](https://docs.webrouter.net/mcp#loading-keys-mid-conversation-load_env).

### Redirects are not followed

Providers that talk to a REST endpoint send the credential as a request header.
`httpx` replays request headers to a redirect target and **does not strip them
when that target is a different host**, so following a redirect would hand the
new host your API key.

web-router therefore pins `follow_redirects=False` on every client it builds
itself — every backend it calls over HTTP directly, which is all but the two
SDK-backed ones — rather than relying on the `httpx` default. A provider that is
sending a credential raises `ConfigurationError` if you try to turn redirects
on:

```python
# With BRAVE_SEARCH_API_KEY set, so the provider is sending a credential:
WebRouter(provider_config={"brave": {"follow_redirects": True}}).search("...")
# ConfigurationError: Provider 'brave' refuses follow_redirects=True while it is
# sending a credential.
```

An endpoint like this has no legitimate reason to redirect, so a redirect is
either a misconfiguration or an attempt to collect the credential. Point
`base_url` at the host you mean to call instead. Keyless providers have nothing
to leak and may follow redirects if you ask them to.

A provider with an unusual auth scheme overrides `_auth_headers` rather than
building the header itself, which keeps `api_key` set — and that is what this
refusal keys off, so a backend cannot opt out of the protection by hiding its
credential from the base class.

## Where your queries and URLs go

A search is sent to the provider that serves it, and that provider sees your
query. An extract is the same: the backend fetches the URL on your behalf, so it
sees every URL you pass — including one that is itself sensitive, such as a
signed or single-use link. Name a provider and it is the one you named.

The default provider, `auto`, chooses for you: the backends you hold a
credential for first, then the anonymous tiers those vendors publish — Tavily's
keyless mode then You.com's open MCP endpoint for a search, Tavily's keyless
mode then Jina Reader for an extract. So **a router you have not configured
still sends queries and URLs to a third party you did not name**, under that
vendor's terms for anonymous use rather than under an account you control.

`response.provider` always names the backend that answered. If that matters for
your data, name a provider explicitly rather than relying on the default.

Extract backends fetch through their own proxy networks, and some cache what
they fetched — Jina Reader may answer from a cached snapshot rather than
fetching, and says so in an unmapped `warning` field that reaches you through
`ExtractedDocument.extra`. Treat an extracted page as having passed through the
vendor, not as something you fetched yourself, and read `extra` when it matters
whether the fetch was live.
