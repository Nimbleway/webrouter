"""Limits on the navigation guard's DNS answer cache, kept apart so constants.py stays at its size."""

from __future__ import annotations

MCP_BROWSER_DNS_CACHE_TTL_S = 1.0
"""How long one resolved answer is reused before the name is looked up again.

The browser does its own lookup after a route lets a request through, so a
short window keeps the guard's rebinding check meaningful while a page's burst
of subresources still shares a single lookup.
"""

MCP_BROWSER_DNS_CACHE_MAX = 128
"""Most names one guard keeps; past that the oldest answer is dropped."""
