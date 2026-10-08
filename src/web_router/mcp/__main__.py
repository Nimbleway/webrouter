"""``python -m web_router.mcp``, the same server the console script runs.

Deliberately nothing but a call. ``serve`` and ``main`` live in
:mod:`web_router.mcp.server` because a package whose ``__init__`` imports its
own ``__main__`` is imported twice under ``python -m`` -- once as the module and
once as ``__main__`` -- which CPython warns about and which leaves two copies of
everything defined here.
"""

from __future__ import annotations

from web_router.mcp.server import main

if __name__ == "__main__":  # pragma: no cover -- a one-line call to a tested function
    main()
