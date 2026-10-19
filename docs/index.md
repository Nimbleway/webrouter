# web-router Technical Documentation

Technical documentation for web-router: one interface over many web search, extraction, research and browser APIs.

## What is web-router?

web-router puts many backends behind one typed interface: 12 search backends, 9 extract backends, 4 research backends, 5 browser-session backends and 3 hosted browse backends, plus an `auto` provider that chains them. Switch backends with one argument; the call, the options and the response shape stay the same.

```python
from web_router import Provider, WebRouter

WebRouter().search("query")                 # auto: no key needed, keyless tiers answer
WebRouter(Provider.NIMBLE).search("...")    # or name one
WebRouter(Provider.TAVILY).search("...")    # same call, same result shape
```

## Documentation sections

### Guides

- **[Adding a Provider](guides/adding-a-provider.md)**: integrate a new search or extract API
- **[Promoting an Option](guides/promoting-an-option.md)**: map a shared parameter to existing providers
- **[Plugin vs Catalogue](guides/plugin-vs-catalogue.md)**: runtime registration vs. built-in providers

### Contract

- **[Contract Overview](contract/README.md)**: request and response models, invariants, and how they evolve

### Integrators

- **[Framework Integration](integrators/frameworks.md)**: integrate once, let users choose providers at runtime

### Agents

- **[MCP Server](mcp.md)**: the self-hosted stdio MCP server and its tools
- **[Baseline Agent Prompt](agent-prompt.md)**: a starting system prompt for agents on web-router's tools

## Quick links

- [Main README](https://github.com/WebContextLayer/webrouter#readme): quickstart, examples, provider support
- [Contributing](https://github.com/WebContextLayer/webrouter/blob/main/CONTRIBUTING.md): setup, tests, PR workflow
- [Examples](https://github.com/WebContextLayer/webrouter/tree/main/examples): runnable scripts for routing, errors, depth, custom providers
- [Changelog](https://github.com/WebContextLayer/webrouter/blob/main/CHANGELOG.md): release history

## Get started

web-router is not on public PyPI. Releases are published to the internal JFrog PyPI index only; [RELEASING.md](https://github.com/WebContextLayer/webrouter/blob/main/RELEASING.md) shows how to install from it. Extras are named after the methods you call (`search`, `extract`, `research`, `browser`, `all`, plus `mcp`, `evals` and `langchain`).

To work from a checkout, and run an example that needs no credentials:

```bash
gh repo clone WebContextLayer/webrouter
cd webrouter
uv run --all-extras python examples/main.py
```

## Contributing

The most useful contributions:

1. **Map a shared option to a provider**: see [Promoting an Option](guides/promoting-an-option.md)
2. **Add a new provider**: see [Adding a Provider](guides/adding-a-provider.md)
3. **Check mappings against live APIs**: report where a provider's docs and its real responses differ

Open an issue before starting a large change: [Issues](https://github.com/WebContextLayer/webrouter/issues).
