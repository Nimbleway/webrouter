"""A model judge's label: what a report records, so its figures combine only with the same judge's."""

from __future__ import annotations

from typing import ClassVar
from urllib.parse import urlsplit


def judge_label(name: str, model: str, base_url: str | None) -> str:
    """``openai:gpt-4o``, or ``openai:gpt-4o@host`` on another endpoint: the same model name elsewhere may differ.

    Only the host and port: credentials in the URL never reach a report.
    """
    parts = urlsplit(base_url or "")
    host = f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname or ""
    return f"{name}:{model}@{host}" if host else f"{name}:{model}"


class EndpointLabel:
    """``label`` for a judge with a ``model`` and an optional ``base_url``: put it before ``Judge`` in the bases."""

    name: ClassVar[str]
    model: str
    base_url: str | None

    @property
    def label(self) -> str:
        return judge_label(self.name, self.model, self.base_url)
