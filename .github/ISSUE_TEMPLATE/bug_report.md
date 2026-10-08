---
name: Bug report
about: Something is not working as documented
labels: bug
---

**What happened**

**What you expected**

**Reproduction**

```python
# minimal snippet
```

**Environment**

- web-router version (`uv run python -c "import web_router; print(web_router.__version__)"`):
- Python version and OS:
- Library or MCP server (and which client: Claude Code, Claude Desktop, Cursor, ...):
- Method (search / extract / research / open_browser / browse):
- Provider (nimble / exa / auto / ...), and for `auto` the one that served (`response.provider`):

Please never paste an API key. For the MCP server, the `web-router://capabilities`
resource lists which backends are configured, by name only, and is safe to share.
