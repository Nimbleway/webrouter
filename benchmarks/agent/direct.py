"""A ``direct:<vendor>`` arm: the vendor's own REST API as the agent's tools, with no web-router in between.

The control for what web-router adds when the vendor has no hosted MCP server
to attach: the agent gets one search tool and one page-read tool, each a plain
HTTP call to the vendor, and reads the vendor's JSON as it comes back -- no
rendering, no character budget, no routing guide. The request is what a
developer following the vendor's own docs would send for an LLM to read, fixed
here and recorded in ``run.json``; the model picks only the query or the URL.

The key is read from the environment and sent only to the vendor, as web-router
would send it. It never reaches the platform, ``run.json`` or the rows.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from web_router.constants import (
    ENV_WEBIQ_API_KEY,
    WEBIQ_API_KEY_HEADER,
    WEBIQ_BASE_URL,
    WEBIQ_BROWSE_PATH,
    WEBIQ_CONTENT_FORMAT,
    WEBIQ_EXTRACT_FORMATS,
    WEBIQ_PARAM_NAMES,
    WEBIQ_SEARCH_PATH,
)

DIRECT_PREFIX = "direct:"
DIRECT_VENDOR = "webiq"
"""The one vendor a ``direct:`` arm serves today: WebIQ, which has no hosted MCP server to attach."""
CLAUDE, OPENAI = "claude", "openai"
CALL_SEARCH, CALL_READ = "search", "read"
DIRECT_TIMEOUT = 60.0
WITHHELD_SIGNATURE = "may hold this question set's own answer key"
"""In every refusal to read an answer-key page, on either kind of arm: how a run tells one from a tool error."""
DIRECT_ERROR = "The {tool} call failed and returned nothing: {error}"
DIRECT_WITHHELD = (
    "The {tool} call did not run: {url} " + WITHHELD_SIGNATURE + ", so this benchmark keeps it from the agent. "
    "Read another page."
)
KEY_MASK = "***"
MSG_DIRECT_NO_KEY = "arm {name!r} needs {env} in the environment: it calls {url} directly"
MSG_UNKNOWN_DIRECT = "unknown direct vendor {name!r}: expected " + DIRECT_VENDOR

# WebIQ's endpoints, header and fields are web-router's own, from its provider's constants. What the arm does
# differently is deliberate: no `maxLength`, so Browse cuts a page at its own 10,000-character default as a
# developer following WebIQ's docs would get it, and the JSON goes to the model unrendered.
SEARCH_TOOL, READ_TOOL = "webiq_search", "webiq_browse"
SEARCH_DESCRIPTION = "Search the web with Microsoft WebIQ. Returns WebIQ's JSON response as it comes back."
READ_DESCRIPTION = "Read one web page with Microsoft WebIQ Browse. Returns its JSON response as it comes back."
RESULTS_FIELD = "webResults"
READ_FORMAT = {"contentFormat": WEBIQ_EXTRACT_FORMATS["markdown"]}


@dataclass
class DirectResult:
    """One direct call's outcome, in the shape the benchmark reads a web-router tool result in."""

    content: str
    is_error: bool = False
    provider: str | None = None
    call: str | None = None
    """``search`` or ``read`` when the vendor answered: what the vendor bills for."""
    withheld: int = 0
    """Results or pages kept from the agent because they may hold the question set's answer key."""
    response: Any = field(default=None, repr=False)

    def event_fields(self) -> dict[str, Any]:
        """The fields a Managed Agents ``user.custom_tool_result`` event carries besides the tool use's id."""
        return {"content": [{"type": "text", "text": self.content}], "is_error": self.is_error}

    def function_call_output(self, call_id: str) -> dict[str, Any]:
        """The Responses API input item that answers the ``function_call`` carrying ``call_id``."""
        return {"type": "function_call_output", "call_id": call_id, "output": self.content}


class DirectTools:
    """A vendor's search and page read as the agent's two tools, and the executor that calls the vendor."""

    router = None

    def __init__(
        self,
        name: str,
        vendor: str,
        platform: str,
        max_results: int,
        withholds: Callable[[str], bool] | None = None,
    ) -> None:
        if vendor != DIRECT_VENDOR:
            raise SystemExit(MSG_UNKNOWN_DIRECT.format(name=vendor))
        self.vendor = vendor
        self.platform = platform
        self.max_results = max_results
        self.withholds = withholds or (lambda url: False)
        self.key = os.environ.get(ENV_WEBIQ_API_KEY) or ""
        if not self.key:
            raise SystemExit(MSG_DIRECT_NO_KEY.format(name=name, env=ENV_WEBIQ_API_KEY, url=WEBIQ_BASE_URL))
        self._client: httpx.AsyncClient | None = None

    def definitions(self) -> list[dict[str, Any]]:
        """The two tools, as a Claude custom tool or an OpenAI strict function."""
        tools = [
            (SEARCH_TOOL, SEARCH_DESCRIPTION, "query", {"type": "string"}),
            (READ_TOOL, READ_DESCRIPTION, "url", {"type": "string"}),
        ]
        out = []
        for name, description, arg, schema in tools:
            parameters = {
                "type": "object",
                "properties": {arg: schema},
                "required": [arg],
                "additionalProperties": False,
            }
            if self.platform == OPENAI:
                out.append(
                    {
                        "type": "function",
                        "name": name,
                        "description": description,
                        "parameters": parameters,
                        "strict": True,
                    }
                )
            else:
                out.append({"type": "custom", "name": name, "description": description, "input_schema": parameters})
        return out

    def config(self) -> dict[str, Any]:
        """What ``run.json`` records: each endpoint and the body sent, the model's input as a placeholder."""
        return {
            "search": {
                "provider": f"{DIRECT_PREFIX}{self.vendor}",
                "endpoint": f"{WEBIQ_BASE_URL}{WEBIQ_SEARCH_PATH}",
                "call": {"json": self._search_body("<query>")},
            },
            "extract": {
                "provider": f"{DIRECT_PREFIX}{self.vendor}",
                "endpoint": f"{WEBIQ_BASE_URL}{WEBIQ_BROWSE_PATH}",
                "call": {"json": self._read_body("<url>")},
            },
        }

    def _search_body(self, query: str) -> dict[str, Any]:
        return {"query": query, WEBIQ_PARAM_NAMES["max_results"]: self.max_results, **WEBIQ_CONTENT_FORMAT}

    def _read_body(self, url: str) -> dict[str, Any]:
        return {"url": url, **READ_FORMAT}

    async def acall(self, name: str, tool_input: Any) -> DirectResult:
        """Run one call the model made; a vendor failure is a result the model reads, as web-router's are."""
        arguments = json.loads(tool_input) if isinstance(tool_input, str) else dict(tool_input)
        if name == SEARCH_TOOL:
            return await self._search(name, str(arguments.get("query") or ""))
        if name == READ_TOOL:
            return await self._read(name, str(arguments.get("url") or ""))
        raise ValueError(f"unknown tool {name!r}: expected {SEARCH_TOOL} or {READ_TOOL}")

    async def _search(self, tool: str, query: str) -> DirectResult:
        payload = await self._post(WEBIQ_SEARCH_PATH, self._search_body(query))
        if isinstance(payload, DirectResult):
            return payload
        results = payload.get(RESULTS_FIELD) or []
        kept = [r for r in results if not self.withholds(str(r.get("url") or ""))]
        if len(kept) != len(results):
            payload = {**payload, RESULTS_FIELD: kept}
        content = json.dumps(payload, ensure_ascii=False)
        return DirectResult(content, False, self.vendor, CALL_SEARCH, len(results) - len(kept), payload)

    async def _read(self, tool: str, url: str) -> DirectResult:
        if self.withholds(url):
            return DirectResult(DIRECT_WITHHELD.format(tool=tool, url=url), True, withheld=1)
        payload = await self._post(WEBIQ_BROWSE_PATH, self._read_body(url))
        if isinstance(payload, DirectResult):
            return payload
        return DirectResult(json.dumps(payload, ensure_ascii=False), False, self.vendor, CALL_READ, 0, payload)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any] | DirectResult:
        if self._client is None:
            # Never follow a redirect: httpx would replay the key to wherever it points.
            self._client = httpx.AsyncClient(base_url=WEBIQ_BASE_URL, timeout=DIRECT_TIMEOUT, follow_redirects=False)
        tool = SEARCH_TOOL if path == WEBIQ_SEARCH_PATH else READ_TOOL
        try:
            response = await self._client.post(path, json=body, headers={WEBIQ_API_KEY_HEADER: self.key})
        except httpx.HTTPError as exc:
            return DirectResult(DIRECT_ERROR.format(tool=tool, error=type(exc).__name__), True)
        if response.status_code >= 300:
            # The vendor's own error body, as a direct integration would show it, with the key masked should the
            # vendor ever echo it: the model reads this, and the row keeps it.
            text = response.text[:500].replace(self.key, KEY_MASK)
            return DirectResult(DIRECT_ERROR.format(tool=tool, error=f"HTTP {response.status_code} {text}"), True)
        try:
            payload = response.json()
        except ValueError:
            return DirectResult(DIRECT_ERROR.format(tool=tool, error="the response was not JSON"), True)
        return payload if isinstance(payload, dict) else {"response": payload}

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
