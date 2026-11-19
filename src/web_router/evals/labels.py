"""A model judge's label: what a report records, so its figures combine only with the same judge's."""

from __future__ import annotations

from typing import Any, ClassVar
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

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """A subclass that sets no ``name`` of its own is labelled by its class, ``GradingFails(openai)``: it may
        grade differently, so its figures must not combine with its parent's. One that grades exactly as its
        parent (one that only meters, say) says so by setting ``name`` to the parent's."""
        super().__init_subclass__(**kwargs)
        if "name" not in cls.__dict__ and getattr(cls, "name", None):
            cls.name = f"{cls.__name__}({cls.name})"

    @property
    def label(self) -> str:
        return judge_label(self.name, self.model, self.base_url)
