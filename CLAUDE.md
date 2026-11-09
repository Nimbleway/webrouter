# CLAUDE.md

You are probably here because a user asked you to clone `web-router` and set it
up. This file is how. If they want to **change** the library — add a provider,
fix a bug, open a pull request — read [AGENTS.md](AGENTS.md) next: it holds the
rules every change here follows.

## What this is

`web-router` puts one typed interface in front of many web APIs: `search`,
`extract`, `run_template` (a backend's saved Extract Template, with
`templates` and `template` to find one), `answer` (a backend's written answer
with its sources, in one call), `research`, `open_browser` (a browser
session) and `browse` (a hosted browser agent). Each call behaves the same
whichever backend serves it.
It ships a Python library and an MCP server. [README.md](README.md) is the full
reference.

## Set it up

The one prerequisite is [uv](https://docs.astral.sh/uv/getting-started/installation/).
Install it if `uv --version` fails; it fetches the right Python itself.

```bash
make setup             # every extra: library, MCP server, browser tools, and Chromium for `local`
cp .env.example .env   # only if .env does not exist yet
```

Then tell the user to open `.env` and fill in the keys they hold. **Never ask
them to paste a key into the conversation, and never write one into a file for
them.** No key is required: search and extract work keyless. Every key added
unlocks more backends and fallbacks, and research, browse and cloud browsers
each need at least one; with a key for every backend, every capability is
available. `.env.example` names each variable and where to get it.

Check it works:

```bash
uv run python -c "import web_router; print(web_router.__version__)"
make test              # unit tests, offline, no keys needed
```

## Use it from the user's own project

If they want the library in another project, install it from PyPI:

```bash
uv add "webrouter[all]"           # their project uses uv
pip install "webrouter[all]"      # any other virtualenv
```

To track `main` instead, install this clone in editable mode, so a `git pull`
here is the whole update:

```bash
uv add --editable "/path/to/webrouter[all]"
pip install -e "/path/to/webrouter[all]"
```

## Add the MCP server

Inside this clone, `.mcp.json` already registers it; the user approves it once.
To use it from **every** project, register it at user scope with the absolute
path of this clone:

```bash
claude mcp add --scope user web-router -- uv run --project /path/to/webrouter --all-extras web-router-mcp
```

`--project` keeps the working directory, so the server reads the `.env` of the
project the user is in. For Claude Desktop, Cursor and other clients, see
[docs/mcp.md](docs/mcp.md). After adding keys, the server needs a restart, or
call its `load_env` tool with the path of the user's `.env`.

The `web-router://capabilities` resource shows which backends are configured,
and its `unlock` map lists each backend still missing a key and the variable
that would add it: ask the model to read it.

## Update

```bash
git pull && make setup
```

## Where to look

- [README.md](README.md) — the API, every backend, and what each option does
- [docs/mcp.md](docs/mcp.md) — the MCP server in depth
- [examples/](examples/) — runnable scripts (`uv run python examples/main.py`)
- [CONTRIBUTING.md](CONTRIBUTING.md) — adding a provider and opening a pull request
