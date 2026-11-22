"""The ``data:`` lines of a server-sent event stream, cut where the stream's own format ends a line.

An event stream ends a line at CRLF, LF or CR and nowhere else. ``str.splitlines`` also breaks at U+2028, U+2029 and
U+0085, which JSON leaves unescaped inside a string and a page's text may hold: a reply carrying one was cut
mid-object, so it read as no reply at all.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from web_router.constants import MCP_SSE_DATA_PREFIX

_LINE_END = re.compile(r"\r\n|\n|\r")


def data_lines(body: str) -> Iterator[str]:
    """What each ``data: `` line of ``body`` carries, in order."""
    for line in _LINE_END.split(body):
        if line.startswith(MCP_SSE_DATA_PREFIX):
            yield line[len(MCP_SSE_DATA_PREFIX) :]
