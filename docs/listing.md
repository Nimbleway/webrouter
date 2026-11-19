# Directory listings: one source of truth

What the MCP directories show for the web-router server. Every listing points at
the package a user installs and runs on their own machine (PyPI) or their own
servers (the `Dockerfile`), never at a hosted endpoint. Copy from here when
submitting or updating one, so all of them say the same thing.

## Shared fields

| Field | Value |
| --- | --- |
| Name | WebRouter |
| Slug | `web-router` |
| One line | One MCP server for every web search and extract API. Runs locally with your own keys and picks the right vendor for each question within your budget. |
| Repository | https://github.com/Nimbleway/webrouter |
| Documentation | https://docs.webrouter.net/mcp-server |
| Package | `webrouter` on PyPI (https://pypi.org/project/webrouter/), command `web-router-mcp` |
| Official registry name | `io.github.Nimbleway/webrouter` |
| Licence | Apache-2.0 |
| Logo | https://raw.githubusercontent.com/Nimbleway/webrouter/main/docs/logo/light.svg (dark backgrounds: `docs/logo/dark.svg`) |
| Language | Python |
| Transports | stdio (default); streamable HTTP for a self-hosted shared server |
| Categories | Search, Web scraping, Research, Browser automation, Developer tools |
| Tags | web search, extract, scraping, research, browser, mcp, self-hosted, bring your own keys |

### Long description

WebRouter puts one typed interface in front of many web APIs and serves it as
an MCP server. An agent gets `search`, `extract`, `answer`, `research`, a
vendor's ready-made scrapers (`live_data`), a hosted browser agent (`browse`),
browser sessions it drives itself (`browser_*`), and `web_run`, which picks the
method for a request in words. Each tool behaves the same whichever backend
serves it, and `auto` falls back to the next backend when one fails; a research or
browse job, once a backend accepts it, stays with that backend until it ends.

It runs on your machine, as a subprocess of your client or as one HTTP server
for your team, and reads your own provider keys from your environment. There
is no hosted service in between: each call goes straight to the backend that
serves it. With no key at all, search and extract still work on free tiers;
every key you add unlocks more backends and fallbacks. Spend caps
(`WEB_ROUTER_MCP_MAX_COST_USD`) and a tool allow-list
(`WEB_ROUTER_MCP_TOOLS`) keep an agent within bounds.

### Tools

`search`, `extract`, `answer`, `research`, `research_status`,
`live_data_sources`, `live_data_source`, `live_data`, `browse`,
`browse_status`, `web_run`, `budget_start`, `budget_status`, `fetch_raw`,
`vendors`, `vendor_endpoint`, `call_vendor`, `load_env`, and the browser tools
`browser_open`, `browser_goto`, `browser_snapshot`, `browser_click`,
`browser_type`, `browser_press`, `browser_screenshot`, `browser_read`,
`browser_close`.

### Environment variables

All optional. Without any, search and extract run on free tiers.

- Provider keys, one per backend, for example `NIMBLE_API_KEY`,
  `TAVILY_API_KEY`, `EXA_API_KEY`, `BRAVE_SEARCH_API_KEY`,
  `PERPLEXITY_API_KEY`, `FIRECRAWL_API_KEY`. The full list, with where to get
  each, is in
  [`.env.example`](https://github.com/Nimbleway/webrouter/blob/main/.env.example).
  Every one is a secret.
- `WEB_ROUTER_MCP_MAX_COST_USD`: a spending ceiling for the session.
- `WEB_ROUTER_MCP_TOOLS`: serve only the named tools.
- `WEB_ROUTER_MCP_TOKEN` (HTTP only): the bearer token clients must send;
  required to listen anywhere but loopback. A secret.

### Install

```bash
uvx --from "webrouter[mcp]" web-router-mcp
```

Claude Code:

```bash
claude mcp add web-router -- uvx --from "webrouter[mcp]" web-router-mcp
```

Codex:

```bash
codex mcp add web-router -- uvx --from "webrouter[mcp]" web-router-mcp
```

Claude Desktop (`claude_desktop_config.json`), Cursor (`~/.cursor/mcp.json`)
and other clients configured by file:

```json
{
  "mcpServers": {
    "web-router": {
      "command": "uvx",
      "args": ["--from", "webrouter[mcp]", "web-router-mcp"],
      "env": {
        "TAVILY_API_KEY": "your key, or leave this entry out"
      }
    }
  }
}
```

Self-hosted over HTTP, in Docker (see
[Serve over HTTP for a team](https://docs.webrouter.net/mcp-server#serve-over-http-for-a-team)):

```bash
docker build -t webrouter-mcp https://github.com/Nimbleway/webrouter.git
export WEB_ROUTER_MCP_TOKEN=$(openssl rand -hex 32)
docker run --rm -p 127.0.0.1:8000:8000 -e WEB_ROUTER_MCP_TOKEN -e TAVILY_API_KEY webrouter-mcp
```

## mcp.so

Submit at https://mcp.so/submit (or as an issue on its GitHub repository, if
the form points there).

- Type: MCP Server
- Name: WebRouter
- URL: https://github.com/Nimbleway/webrouter
- Description: the one line above
- Server config: the JSON block above

## PulseMCP

Submit at https://www.pulsemcp.com/submit. It also reads the official MCP
Registry, so the entry may already be there under
`io.github.Nimbleway/webrouter`; search for it first and submit only if it is
missing or wrong.

- URL: https://github.com/Nimbleway/webrouter
- Name, one line and long description: as above

## cursor.directory

Submit at https://cursor.directory/mcp (the "Submit" button on the MCP page).

- Name: WebRouter
- Description: the one line above
- Link: https://github.com/Nimbleway/webrouter
- Logo: the logo URL above
- Config: the JSON block above
- One-click install link (the same server, no keys; users add theirs under
  `env` afterwards):

  ```
  cursor://anysphere.cursor-deeplink/mcp/install?name=web-router&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJ3ZWJyb3V0ZXJbbWNwXSIsIndlYi1yb3V0ZXItbWNwIl19
  ```

  The `config` value is the base64 of
  `{"command":"uvx","args":["--from","webrouter[mcp]","web-router-mcp"]}`.

## Smithery

Not listed. Smithery closed its publishing tools on 31 October 2026 and now
keeps a curated registry of verified servers reached at their vendors' own URLs
(https://smithery.ai/future), with no place for a server that runs locally.
