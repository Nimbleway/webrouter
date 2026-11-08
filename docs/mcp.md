# The web-router MCP server

A self-hosted stdio MCP server that gives an agent — Claude Code, Claude
Desktop, Cursor, anything that speaks MCP — the methods this package
routes: `search`, `extract`, `answer`, `research` and a vendor's templates (`templates`,
`template`, `run_template`) — plus `browse` for a hosted browser agent, the
`browser_*` tools for driving a browser yourself, and `web_run`, which picks the
method for a request in words.

It runs as a subprocess of your own client, on your own machine, and reads your
own credentials out of your own environment. There is no hosted service in the
middle and nothing to sign up for: no key ever leaves the process you started.

## Start with no credentials at all

The default `auto` chain falls back to backends that need no key, so the first
run needs nothing but a clone and the command:

```bash
git clone https://github.com/WebContextLayer/webrouter.git
cd webrouter && make setup
uv run web-router-mcp
```

That is a working `search` and a working `extract` with no account, no key and
no signup — the same keyless tiers the library uses, reached through MCP. Point
a client at it:

```bash
claude mcp add --scope user web-router -- uv run --project /path/to/webrouter --all-extras web-router-mcp
```

or, for a client configured by file (Claude Desktop, Cursor):

```json
{
  "mcpServers": {
    "web-router": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/webrouter", "--all-extras", "web-router-mcp"]
    }
  }
}
```

`--project` points uv at your clone and keeps the working directory your client
chose, so a `.env` in the project you're working in is the one the server reads.
Inside the clone itself, the committed `.mcp.json` already starts it.

`--all-extras` turns on every tool, the `browser_*` ones included; `make setup`
also installs the Chromium the `local` browser drives. Without Playwright the
server does not list the browser tools at all.

## Then add whichever keys you have

Credentials come from the environment of the process your client launches, and
then from one `.env` file. An installed server has no repo beside it and starts
in whatever directory the client picks, so at startup it looks in four places,
in this order, and loads the first file that sets a provider credential:

| # | Where | For |
| --- | --- | --- |
| 1 | the path in `WEB_ROUTER_ENV_FILE` | pointing a client's config at a `.env` you already keep somewhere |
| 2 | `.env` in the working directory | a checkout of this repo, where `.mcp.json` starts the server at the root |
| 3 | the path `load_env` recorded with `remember: true` | a file named in a conversation, read again on every start |
| 4 | `.env` in the user config directory | one file for every client and every project |

The user config directory is `~/.config/web-router` on Linux (or
`$XDG_CONFIG_HOME/web-router`), `~/Library/Application Support/web-router` on
macOS and `%APPDATA%\web-router` on Windows; the server never creates it except
to record a remembered path. A file that sets no provider credential does not
count as found — a project's own `.env`, in whatever directory a client starts
the server in, does not hide yours — and a `WEB_ROUTER_ENV_FILE` that names
no file is reported rather than silently skipped. With no file anywhere the
server starts keyless.

So, from Claude Code, either name a file you already have:

```bash
claude mcp add --scope user web-router -e WEB_ROUTER_ENV_FILE="$HOME/work/.env" -- uv run --project /path/to/webrouter --all-extras web-router-mcp
```

or put your keys in the user config directory's `.env` (copy
[`.env.example`](../.env.example) and fill in the ones you hold) and register
the server with no variable at all. For a client configured by file, the
variable goes in its `env` block:

```json
{
  "mcpServers": {
    "web-router": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/webrouter", "--all-extras", "web-router-mcp"],
      "env": {
        "WEB_ROUTER_ENV_FILE": "/Users/you/work/.env"
      }
    }
  }
}
```

The `env` block can carry the keys themselves too — `"TAVILY_API_KEY": "tvly-..."`
— and a variable the client exports always keeps its value over any file's.

Whichever file it is, only the provider credentials the package reads are taken
from it — the variables every `*_ENV_BY_PROVIDER` table in `constants.py`
names, listed in `.env.example` and in the configuration table in
[README.md](../README.md). A switch such as `WEB_ROUTER_MCP_ALLOW_FILE_URLS` or
`CHROME_PATH` in a file is ignored, because a server registered at user scope
starts in any workspace and a workspace's file is not its operator; `${...}` is
not expanded, so a file cannot copy another secret into a provider's key. The
server never writes a credential anywhere, never sends one to anything but the
backend it belongs to, and never reports one back to the model.

### Loading keys mid-conversation: `load_env`

A model can load a file itself. `load_env` takes the file's **path** — the
model asks you where your `.env` is, and never asks you to paste a key — and
loads its provider credentials under the same rules, without a restart:

```
load_env(path="~/work/.env")                 -> {"file": "/Users/you/work/.env",
                                                 "loaded": ["BRAVE_SEARCH_API_KEY"],
                                                 "configured": {"search": ["auto", "brave", "you-mcp"], ...}}
load_env(path="~/work/.env", remember=true)  # ...and read it on every start
```

It returns the variables it loaded by name and the backends now configured per
method — never a value. A path with no file, one that is not a small text file,
or one that sets no provider credential comes back as a tool error saying so,
and loads nothing; pointed at any other file, it takes nothing from it and
quotes none of it. The router is rebuilt for every later call, so a newly
configured backend joins `auto` at once, and the server sends
`notifications/tools/list_changed` so the client lists the tools again with the
new `provider` enums. Browser sessions already open, and research or browse
jobs still being polled, carry on where they started.

`remember: true` records the path — the path, not a copy of the keys — in
`env-path` in the user config directory, read before that directory's own
`.env`. Delete that file to forget it. A key rotated in the original file takes
effect on the next start.

A key already set is never replaced, so a load can only add backends. That is
also its one risk: a model steered by a page it read could call `load_env` on a
file someone planted on this machine, adding a key for a backend you hold none
for, and later calls through `auto` could then run on that account. The result
names every variable a load set, and the capabilities resource lists every load.

### What the server found

The `web-router://capabilities` resource carries an `env` object alongside the
backend lists:

```json
"env": {
  "searched": [
    {"source": "WEB_ROUTER_ENV_FILE", "path": "/Users/you/wrong/.env", "status": "missing"},
    {"source": "working directory", "path": "/Users/you/project/.env", "status": "no credentials"},
    {"source": "user config", "path": "/Users/you/Library/Application Support/web-router/.env", "status": "used"}
  ],
  "file": "/Users/you/Library/Application Support/web-router/.env",
  "explicit_file_missing": true,
  "loaded": ["TAVILY_API_KEY"],
  "from_environment": ["NIMBLE_API_KEY"],
  "loaded_at_runtime": []
}
```

— every place it looked, in order, and what it found there (`used`, `missing`,
`unreadable` with a `reason`, `no credentials`, or `not read` after an earlier
one was used); the file it loaded; whether `WEB_ROUTER_ENV_FILE` named a file
that is not there; the variables loaded from that file and those the client
already exported, by name; and each `load_env` since. With no provider key at
all it also carries a `hint` saying how to add one, the server's instructions
and the `search` description say no keys were found and that the model can ask
for a `.env` path and call `load_env` — or carry on keyless — and every other
place the server reports keys says the same thing: names and paths, never
values.

Adding a key changes what the tools offer, because the tools are generated from
what is configured — see below. `load_env` applies one at once; one changed in
the client's `env` block or a file the server already read takes a restart.

## The tools

| Tool | What it is for | Latency |
| --- | --- | --- |
| `search` | Find pages: ranked links with their titles and page text. | about a second |
| `extract` | Read pages you already have URLs for, as markdown, HTML or text. `provider="max"` reads through every configured backend at once and returns the first answer, at one fetch per backend per URL. | about a second |
| `templates` | List a vendor's templates: saved, site-specific extractions, each turning one kind of page into a record. Each row names the inputs a run must pass (`required_inputs`) and one input the vendor recorded a run with (`sample_inputs`). `provider` is required, as on the two tools below. Paged with `limit` and `offset`. | about a second, and free |
| `template` | Describe one template: the `input_schema` a run must pass as `inputs`, the `output_schema` of the record, and `samples`, recorded runs to copy a first call from. | about a second, and free |
| `run_template` | Run a template on one page and get its record back. | about a second, and it bills one template run -- on Nimble, one flat rate for every template, on [its pricing page](https://www.nimbleway.com/pricing) |
| `answer` | One question, answered by the backend in a few sentences, with the sources it rests on. A source the answer cites inline carries the marker in `field`. Unnamed, the configured answer backends are tried in ranked order, and one that fails is passed over. | a few seconds; each backend tried bills one call |
| `research` | Start an agentic run that searches, reads and cross-checks over many turns. Waits 25 seconds, then hands back a job handle. | **minutes, and it bills for the run** |
| `research_status` | Poll a handle from `research`: its state, or the answer once it has one. | about a second, and free |
| `browse` | Hand a task to a hosted browser agent that clicks, types and reads through pages. Waits 25 seconds, then hands back a job handle. | **minutes, and it bills for the task** |
| `browse_status` | Poll a handle from `browse`: its state, or the answer and the agent's steps once it has one. | about a second, and free |
| `fetch_raw` | The backend's full, unshaped payload for a handle from any of the above. | instant — nothing is called again |
| `load_env` | Load provider keys from a `.env` file the user names, by path; see [above](#loading-keys-mid-conversation-load_env). | instant |
| `web_run` | Hand a request in words to web-router's router, which picks the method and backends and runs it; `dry_run` returns the plan only. See [Routing and budgets](#routing-and-budgets-web_run-budget_start-budget_status). | as the method it picks |
| `budget_start` | Open a task budget, in dollars and seconds, that every billed tool then spends from. | instant |
| `budget_status` | What the budget has spent, reserved and left. | instant, and free |
| `browser_open` | Open a browser session and get a `session_id` for the tools below. | a few seconds; **a cloud backend bills per minute until it is closed**, `local` is free |
| `browser_goto` | Load a URL in the session. | as long as the page takes |
| `browser_snapshot` | The page's accessibility tree, each element tagged `[ref=eN]` to act on. | about a second |
| `browser_click` | Click an element by `ref` or `selector`. | about a second |
| `browser_type` | Type into a field by `ref` or `selector`; `submit` presses Enter. | about a second |
| `browser_press` | Press a key on whatever has focus. | instant |
| `browser_screenshot` | A PNG of the viewport or the whole page, returned as image content. | about a second |
| `browser_read` | The page as it is now, as text or HTML, shaped like `extract`. | about a second |
| `browser_close` | End the session and stop paying for it. | about a second |

The descriptions the model reads say the same thing, because with twenty-four tools in
front of it the model *is* the router: what it needs to choose on is what each
tool is for, what it costs and how long it takes.

Arguments mirror the library's own, name for name — `query`, `max_results`,
`search_depth`, `include_domains`, `exclude_domains`, `country`, `locale`,
`urls`, `format`, `template`, `inputs`, `name`, `limit`, `offset`,
`output_schema`, `records`, `effort`, `task`, `start_url`,
`max_steps`, `allowed_domains`, `provider`, and for the browser tools the
handle's own — `url`, `wait_until`, `ref`, `selector`, `text`, `submit`, `key`,
`full_page`, `format`, `timeout`. Anything the library refuses, this refuses in
the library's own words.

Over MCP the model chooses `max_results`, not you. Its description steers it
away from a single result, but the choice stays the model's. A host that wants
the number fixed should [wrap `search` itself](../README.md#wrapping-search-for-your-own-agent).

### `research` never blocks the session

A research run takes minutes and an MCP client talks over one pipe. So
`research` submits the run, waits 25 seconds, and either answers outright or
hands back a handle with the run still going. Poll `research_status` with that
handle — calling `research` again would start, and pay for, a second run.
`browse` and `browse_status` work the same way, and each status tool refuses a
handle from the other.

### Routing and budgets: `web_run`, `budget_start`, `budget_status`

Experimental, as routing is in the library.

**`web_run(prompt, max_cost_usd?, time_limit_s?, dry_run=false)`** is
`AsyncWebRouter.run` over MCP: the router plans the prompt -- the method, its
options and the backend order -- and the server runs it. The result is the
method's own tool output (`search`'s, `extract`'s, ...) with `tool`, the
method that ran, a `handle` for `fetch_raw`, and the `decision` record.
`dry_run=true` returns the plan alone and calls nothing. `max_cost_usd` and
`time_limit_s` cap that one call, planning included, within the budget
below; each must be a positive, finite number. When the router picks
`research` or `browse`, `web_run` waits as those tools do, 25 seconds or less
if its own `time_limit_s` or the budget has less left, and then hands back a
handle for `research_status` or `browse_status`. The other tools are
unchanged: `search`, `extract` and the rest never ask the router.

The router is set by the operator, once, when the server starts:

- unset, the built-in rules router (`web_router.routing.rules.rules`): a prompt
  with an `http(s)` URL is read with `extract`, anything else searched;
- `WEB_ROUTER_MCP_ROUTER=<module>:<attribute>` names a router in a module you
  installed -- any form `router=` takes on the library;
- `WEB_ROUTER_MCP_ROUTER=off` attaches none: `web_run` then plans the static
  order, which is `search`.

`WEB_ROUTER_MCP_ROUTER` imports and runs your code when the server starts, so
set it in the server's own process environment, never in a project's config
or a `.env` file.

Plans hold to `url_policy="public_only"`: a URL in a prompt that names a
loopback, private or cloud-metadata host is never fetched or passed to a
backend as a URL. It holds for `web_run`'s plans only. The direct `extract`
and `browse` tools send the URLs the agent passes as they are, and the browser
tools have their own guard ([Where the browser may go](#where-the-browser-may-go)),
which checks DNS answers and redirects but refuses only cloud metadata hosts
and the browser's debugging port: other loopback and private hosts are
admitted there. A refused URL stays in the prompt text, though, and the prompt
itself goes to the backend that serves it, as its search query or browse task, as it
would through `search`. The built-in router runs in the server's process and
sends nothing out; an operator's router runs there too, but it is your code,
and may send the prompt on, to a model's server for instance.

**The budget.** Every billed tool -- `search`, `extract`, `answer`, `research`,
`browse`, `run_template`, `browser_open` and `web_run` -- spends from one budget
per connection, a shared budget as `wr.budget(...)` is in the library. Each
attempt reserves its estimated list price before it starts, and a call that
does not fit is refused with `BudgetExceededError` before anything bills. A
template run is charged its list price; a cloud browser per minute of its
lifetime (`timeout`), rounded up to whole minutes, on the dearest backend that
can serve the options asked for. A browser option that can turn on an extra
billed apart -- a proxy country, stealth, CAPTCHA solving, a profile -- makes a
paid backend's price unknown. Under a cost cap a backend whose price is
unknown never runs, and the refusal names the unknown price. So `extract` with
`provider="max"`, which races every member at once and has no price before the
race, is refused under a cost cap; name a backend or use `auto`. The calls the
server charges itself run on the budget's clock, and `research` and `browse`
wait no longer than it has left.

- **The operator's ceiling**: `WEB_ROUTER_MCP_MAX_COST_USD` and
  `WEB_ROUTER_MCP_TIME_LIMIT_S`. With no `budget_start`, the whole connection
  is one task under it, its clock running from the connection's start. The
  agent cannot reset or raise it.
- **`budget_start(max_cost_usd, time_limit_s)`** opens a new task inside the
  ceiling, with both limits stated each time -- positive, finite numbers, or
  the call is refused -- and a clock from now. Each
  reservation must fit the task and the ceiling, so the limits are cut to what
  the ceiling has left. A call still running when a new task starts stays
  charged to the connection.
- **`budget_status()`** returns the task in force, the connection's totals
  and the ceiling:

  ```json
  {
    "budget": {"max_cost_usd": 1.0, "time_limit_s": 300, "spent_usd": 0.016,
               "reserved_usd": 0.0, "remaining_usd": 0.984,
               "elapsed_s": 12.5, "time_left_s": 287.5,
               "connection": {"spent_usd": 0.032, "reserved_usd": 0.0}},
    "ceiling": {"max_cost_usd": 5.0, "time_limit_s": null}
  }
  ```

  `budget` is `null` with no ceiling and no `budget_start`. Its
  `max_cost_usd` and `time_limit_s` are those in force, cut to the ceiling,
  and `remaining_usd` counts against the ceiling too; `connection` is what
  every task on the connection has spent and reserved. `ceiling` is always
  there, each limit `null` when the operator set none.

With no ceiling and no `budget_start`, nothing is held, and every tool runs as
it would with no budget.

The three variables are read from the server's own process environment, once,
at startup. No `.env` file and no `load_env` call can set or change them, and a
value the server cannot read -- an empty one included -- stops it from
starting rather than leaving the connection without the ceiling you meant. The capabilities resource reports
all of it under `routing`: the router (`rules`, `operator` for yours -- never
its `<module>:<attribute>` -- or `off`), where it runs (`router_runs`: `in_process` for the built-in router or
none, `operator` with a `router_note` for yours), a `prompt_note` saying the
prompt goes to the backend, the `url_policy` with a `url_policy_note` saying it
holds for `web_run` alone, the ceiling, and the budget in
force (`null` with none).

### A browse result never carries its session

A browse task's payload can include the session the agent ran in — a live view
that controls the browser without a key, a CDP URL with the account key in its
query string. So every browse output — the shaped result, `browse_status`,
`fetch_raw` on a browse handle — and every tool's error message, whatever the
tool (a vendor's HTTP error body can ride on any method's failure) — replaces
these with `[redacted]`:

- **Everything beneath a credential-named field**, however nested: `token`,
  `apiKey`, `signingKey`, `jwt`, `secret`, `password` / `passwd`,
  `privateKey`, `accessKey`, `authorization`, `auth`, `cookie` / `set-cookie`,
  `credentials`, and the plural or `*Token` / `*ApiKey` forms of them.
- **Any URL beneath a session-named field**: a live view, debugger, devtools,
  inspector, CDP, websocket, connect or connection endpoint — `liveViewUrl`,
  `debuggerFullscreenUrl`, `wsEndpoint`, `connectUrl`, `CDPURL` and the like,
  matched in any case, with or without separators.
- **Any URL that is a session on its face**, wherever it appears, free text
  included: a `ws://` / `wss://` scheme; a username or password in it;
  `devtools`, `cdp`, `live` or `debug` in its host or path, as in
  `http://127.0.0.1:9222/devtools/browser/…`; or a `token`, `apiKey`,
  `x-api-key`, `signingKey`, `jwt`, `key`, `auth`, `access_token`,
  `signature` or `sig` query or fragment parameter.

The result's `note` says how many values were replaced. Every other handle's
payload is returned verbatim, except `browser_read`'s, below.

## Driving a browser

`browse` hands a whole task to someone else's agent. The `browser_*` tools are
for when the model is the agent: it opens a session, looks at the page, and
decides every click itself.

```
browser_open(provider="local")              -> {"session_id": "browser-Xq3…", "state": "open", …}
browser_goto(session_id, url="https://news.ycombinator.com")
browser_snapshot(session_id)                -> the tree, each element tagged [ref=e14]
browser_click(session_id, ref="e14")        # or selector="a.morelink"
browser_type(session_id, text="web-router", ref="e3", submit=True)
browser_screenshot(session_id)              -> an image
browser_close(session_id)
```

### The server holds the session; the model holds an id

A model cannot hold a browser handle or run a `with` block, so the server keeps
every session it opened in a table and gives the model a random id it minted,
`browser-…`. That id is all the model ever sees. The CDP URL — a credential:
whoever holds it drives the browser, and several backends put the account key
in it — the backend's own session id and any live-view URL stay in the server.
Every browser tool's output and error message goes through the same redaction
as a browse result, and on top of it every one of those values is replaced
literally wherever it appears, so a page that quotes its own debugging endpoint
still does not hand it over. `browser_read` stores the page already scrubbed,
so `fetch_raw` on its handle can only return what is safe.

Each session keeps one handle and one CDP connection for its whole life —
several backends end a session when its CDP client disconnects — and runs one
action at a time.

### What `browser_open` takes, and what it does not

`provider` (the browser backends configured here, `local` always among them),
`headless`, `timeout` (the session's lifetime: 600 seconds when unset, and at
most 600 however it is set), `idle_timeout`, `viewport`, `proxy_country`,
`stealth` (`"off"`, `"low"` or `"high"`) and `solve_captchas` — but only the
ones a backend configured here maps, as for every other tool, and `stealth`
only at the levels one of them serves. `auto` is read as the chain it would
try, since it declares everything and routes each request to a member: with no
key at all that is `local`, so the schema offers `headless`, `timeout` and
`viewport` and nothing else. A backend refuses an option it does not map, or a
value of one it cannot serve, rather than ignoring it — an option sent anyway
reaches the library and its error, never a silent drop.

With no `provider`, `auto` picks one, and `stealth` at any level (`"off"`
included), `proxy_country` or `solve_captchas` pass over `local`, which offers
none of them. Only a cloud
vendor can serve them, and every cloud vendor **bills for the session**. The
`browser_open` description says so to the model; leave those three unset, or
pass `provider="local"`, to stay free.

It deliberately does **not** take `provider_options`, which reach the backend
untranslated — for `local` they are Chrome command-line switches — or `profile`
/ `persist_profile`, a saved, logged-in identity you own. Those are set in code,
never from a conversation, and passing one is refused by name.

The tools need Playwright: install `web-router[mcp,browser]`. Where it is
missing the server leaves every `browser_*` tool out of its tool list, and a
client that calls `browser_open` anyway is refused before a session is opened —
and billed for — that nothing could drive.

### Where the browser may go

A model driving a browser takes its instructions from the pages it reads, so a
page can tell it where to go next. On `local` that browser runs on your machine,
beside its own debugging endpoint, your files and, on a cloud VM, the metadata
service that hands out the machine's credentials. So the browser tools open
**only `http:` and `https:` pages and `about:blank`**, and refuse:

| Refused | Why |
| --- | --- |
| `file:` | a page could have the model read your own files into the transcript |
| `chrome:`, `chrome-extension:`, `devtools:`, `view-source:`, `javascript:`, `data:`, `blob:` and any other scheme | browser internals, or a way to run script the page did not serve |
| the session's own CDP host and port, under any loopback name | its `/json` endpoint hands out the socket that controls the browser |
| cloud metadata hosts, and all of `169.254.0.0/16` | they answer anything running on the machine with its credentials |

What is deliberately **not** refused: loopback and private-network (RFC 1918)
addresses, beyond the session's own CDP endpoint and the metadata ranges above.
A dev server on `localhost:8000` or an intranet page is a legitimate thing to
point a browser on this machine at — but it also means a page can steer the
model toward one, and what it serves enters the transcript. Run the server
(and pick `local` sessions) on a machine whose network holds nothing a
page-steered model should not read.

The policy is enforced at these points:

- `browser_goto` refuses a URL before the page is touched. Decimal, hex, octal
  and short IPv4 spellings (`2130706433`, `0x7f000001`, `127.1`) are read as
  the address they spell.
- A Playwright route on the browser context sees each request's *first* hop and
  aborts one to a refused address — a link, a form, a script's `fetch`, a
  WebSocket. It does not see redirect hops: Playwright routes only the first
  request of a chain. For a host that is a name rather than an address, the
  route also resolves it, on a worker thread, and aborts the request if any
  address it resolves to is link-local or a metadata address, or is the
  session's CDP host on the CDP port — so `http://my-alias.example:9222/json`
  pointing at `127.0.0.1` is refused like `http://127.0.0.1:9222/json`.
- The server records, while an action runs, every navigation request —
  redirect hops included — and every committed frame navigation, in the main
  frame or any subframe, that reaches a refused address. That catches a frame
  sent somewhere refused and removed again before the action ends.
- After the action it checks the URL of the page and of each frame still
  attached, and resolves every DNS name the recorded navigations touched — so a
  redirect hop or a frame sent to an *alias* of a refused address, which the
  route never resolved, is still caught before the action's result is returned.

If any of these finds something, the page is sent back to `about:blank` and the
action returns a refusal instead of its result, so nothing the action read is
handed to the model.

What this does not guarantee: a subresource that is *not* a navigation — an
image, a script's `fetch` — redirected to a refused address is not aborted,
because the route never sees the hop; what the browser's own rules allow the
page to do with the response is up to the browser. Only the `browser_*` tools'
results are held back: a page's scripts ran, and anything they sent elsewhere
was sent. The checks watch the session's own page and context; a popup's frames
are covered by the request record and the route, not by the frame checks.
Blocking the CDP port inside the browser as well, through CDP
`Network.setBlockedURLs`, was tried and did not stop a redirect hop over a
`connect_over_cdp` connection, so it is not used.

The DNS check narrows DNS rebinding; it does not close it. The route's lookup
and the browser's are two separate lookups: once the route lets a request
through, Chrome resolves the name again with its own resolver and cache, and
connects to whatever that answer is. A name served with a short TTL can answer
the route with a public address and the browser, a moment later, with
`169.254.169.254` or `127.0.0.1`. Nothing pins the browser's connection to the
address that was checked, so a server that times its answers can still reach
a refused address. What the check does rule out is the plain alias — a name
that always resolves to a refused address, whether it was a first hop the route
saw or a redirect hop resolved when the action settled — and a rebinding that
has already flipped by the time it is looked up. Other limits of the same
check: a name that does not resolve here is let through (the browser's lookup
fails the same way, unless its resolver — DNS over HTTPS, say — differs from
the system one); and a CDP host that is itself a name, as on a cloud vendor, is compared by name
and loopback alone, not by the addresses it resolves to.

An operator who needs local files — a test fixture, say — sets
`WEB_ROUTER_MCP_ALLOW_FILE_URLS=1` where the server runs. It is an environment
variable and never a tool argument, so a conversation cannot turn it on, and it
opens `file:` alone.

### Snapshots, refs and errors

`browser_snapshot` returns the tree as its own text block, capped at **40,000
characters** (about 10,000 tokens) unless the call passes `max_chars`. That is
far more than the 8,000 a search result gets, because a ref past the cut cannot
be clicked; it holds the whole tree of a typical article, results page or form.
A cut tree says so in `truncated` and `note`.

A ref is good until the next snapshot or navigation. `browser_click` and
`browser_type` check before touching the page that exactly one of `ref` and
`selector` was given and that the ref looks like one (`e14`, not `[ref=e14]`).
A failed action comes back as an error saying what to do next: a stale ref says
to take a new snapshot, a missing selector to check it or use a ref, a timeout
to retry or pass a larger `timeout`. A page or connection that is gone releases
the session and says to open a new one.

### Nothing is left running

| Limit | Value | What happens |
| --- | --- | --- |
| Idle timeout | 5 minutes without a call | the session is released; its id then says why |
| Session lifetime | 600 seconds, and never more | sent on every `browser_open`, so the backend ends the session itself even if this server is gone |
| Concurrent sessions | 3 | a fourth `browser_open` is refused, naming the open ones |
| One action | 30 seconds unset, 120 at most | a larger `timeout` is lowered to 120; a running action keeps its session from being reaped |
| Snapshot size | 40,000 characters unset, 160,000 at most | a larger `max_chars` is lowered to 160,000 |
| Opening | 90 seconds | a cancelled `browser_open` still finishes, then releases the session it opened |
| Shutdown | stdin closing, SIGTERM, SIGINT or SIGHUP | every session is released, within 30 seconds, before the process exits |

The input schemas carry the same maximums, so a client that validates arguments
refuses a larger value before the server sees it.

A model that forgets `browser_close` costs five minutes of a session, not an
afternoon. What the server cannot do is release a session after it stops
running: **SIGKILL**, a crash of the interpreter, or a client that kills the
server sooner than the 30 seconds the release may take leaves sessions behind.
A cloud session bills until the vendor ends it at the end of its lifetime,
which is why every session is opened with one; a session a vendor started while
`browser_open` timed out is covered the same way. A **`local` Chrome is
orphaned**: its lifetime is a timer inside the server process, so it dies with
the process, and the browser keeps running until you quit it (`pgrep -fl
remote-debugging-port` finds it).

## The tools describe *your* install, not the package

Every install holds a different set of keys, so the `provider` argument's enum
is generated when the client lists the tools, from
`configured_providers()` / `configured_extract_providers()` /
`configured_research_providers()` / `configured_answer_providers()`. Hold only a Tavily key and that is what the
enum says; the model is never shown thirteen backends it cannot reach, and
cannot pick one that is certain to throw.

With nothing configured at all the enums read:

```
search   ["auto", "you-mcp"]     # keyless
extract  ["auto", "jina", "max"] # keyless; max races whatever auto would try
template []                      # no keyless tier and no auto: a template's name belongs to one vendor
research ["auto"]                # every research backend bills, so nothing here can run
answer   []                      # every answer backend bills, and there is no auto yet
```

and the `research`, `answer` and template tools' own descriptions say so, in as many
words, rather than pretending to be tools that work.

The resource `web-router://capabilities` carries the same lists as JSON, plus `browse` and `browser`
-- the three template tools under one `template` key --
so a client can learn what this install can do without a round trip through the
model — and the `env` object [above](#what-the-server-found), saying where those
keys came from.

## Result shaping: the one place this differs from the library

The library's rule is that nothing is thrown away — full page bodies, and the
backend's untouched response under `raw`. Inside a context window that rule is
actively harmful: one deep search can be larger than the window it is read in.

So a shaped result:

- keeps the fields a reader acts on, and drops `description` where `content`
  already holds the same text;
- caps the whole response at **8000 characters** (`WEB_ROUTER_MCP_MAX_CHARS`
  overrides it);
- cuts long text at a word boundary and marks the cut ` ... [truncated]`;
- drops whole results from the end of a list only when trimming text is not
  enough, and never drops the last one;
- **never inlines `raw`**;
- says in the result itself that it was shortened, and which handle gets the
  rest.

Every result carries a `handle`. `fetch_raw` takes it and returns the backend's
untouched payload, uncapped — nothing is lost, it is just not spent on the
context window unless you ask for it. The session keeps the 64 most recent
handles and evicts the oldest, so a long conversation stays bounded.

Everything else — parameter names, provider selection, error messages — is a
thin projection of the library. A failure comes back as a tool error carrying
the library's own message, which already names the environment variable to set:

```
[tavily] No API key for provider 'tavily'. Set the TAVILY_API_KEY environment
variable, or pass provider_config={'tavily': {'api_key': ...}} ...
```

## Environment variables the server itself reads

| Variable | Default | Meaning |
| --- | --- | --- |
| `WEB_ROUTER_ENV_FILE` | unset | The `.env` file to load provider keys from, looked at before any other; see [Then add whichever keys you have](#then-add-whichever-keys-you-have). Read from the real environment only — no file can set it. |
| `WEB_ROUTER_MCP_MAX_CHARS` | `8000` | How large one shaped tool result may be. An unreadable value falls back to the default rather than stopping the server. |
| `WEB_ROUTER_MCP_ALLOW_FILE_URLS` | unset | `1` lets the browser tools open `file:` URLs. Off by default; see [Where the browser may go](#where-the-browser-may-go). |
| `WEB_ROUTER_MCP_ROUTER` | the rules router | The router `web_run` plans with: `<module>:<attribute>`, or `off`. See [Routing and budgets](#routing-and-budgets-web_run-budget_start-budget_status). |
| `WEB_ROUTER_MCP_MAX_COST_USD` | unset | The ceiling on what one connection may spend, in US dollars at estimated list prices. |
| `WEB_ROUTER_MCP_TIME_LIMIT_S` | unset | The ceiling on how long one connection may run billed calls, in seconds from its start. |

Everything else it reads is a provider credential, and belongs to the provider.

## Sharing it with your team

Three ways, and only the middle one asks a colleague to type anything.

### They work in this repo: nothing to install

`.mcp.json` at the repo root is committed, so a colleague who clones this
repository and opens Claude Code in it is offered the server straight away and
approves it once. No install command, no config file to write: the committed
command is `uv run --all-extras web-router-mcp`, and `--all-extras` is what lets
it work before anyone has run `make setup` -- a bare `uv run` on a fresh
clone syncs only the base dependencies, and the server's SDK arrives with the
`mcp` extra.

The server's entry point loads the gitignored `.env` from the directory it is
launched in -- the repo root, when `.mcp.json` starts it -- so keys put there
reach it without being exported in anyone's shell; it is the second place of
the [four it looks](#then-add-whichever-keys-you-have). The library itself
never reads dotfiles: this happens in `web-router-mcp`'s `main()` and its
`load_env` tool alone. A variable already exported wins over the file, and a
missing file is a no-op, so someone who has just cloned and has no `.env` yet
still gets the keyless chain. Only provider credentials are read -- a server
registered at user scope can be started from any workspace, and a workspace's
`.env` must not be able to flip a policy switch like
`WEB_ROUTER_MCP_ALLOW_FILE_URLS` or point `CHROME_PATH` somewhere; those come
from the real environment, set by whoever runs the server.

### They want the tools everywhere: one command

`.mcp.json` only applies inside this repository. To reach the tools from any
project on their machine, a colleague registers it once at user scope:

```bash
claude mcp add --scope user web-router -- uv run --project /path/to/webrouter --all-extras web-router-mcp
```

### Keys for a server started anywhere

Their keys then come from `WEB_ROUTER_ENV_FILE` in that block's `env`, from a
`.env` in their user config directory, or from `load_env` in a conversation —
see [Then add whichever keys you have](#then-add-whichever-keys-you-have).

### What cannot be shared

Each person supplies their own credentials. There is no central key store and
there cannot be one -- that is what "self-hosted" buys: nobody hands anyone else
their Tavily or Exa key, and no key leaves the machine it was set on. The
install friction is removable; the credential friction is not, and only applies
to the paid backends. Search and extract work on the first run with nothing set.
