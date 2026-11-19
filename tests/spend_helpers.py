"""A spend sink that keeps what it is given, shared by the spend log's tests."""

from __future__ import annotations

from web_router import SpendEntry


class Kept:
    """A sink holding every entry it records, in order."""

    def __init__(self) -> None:
        self.entries: list[SpendEntry] = []

    def record(self, entry: SpendEntry) -> None:
        self.entries.append(entry)
