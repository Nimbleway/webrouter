"""Whether backend credentials and settings may come from the process environment.

A router built with ``use_environment=False`` turns this off while it builds a
backend or reads which backends are configured, and every backend built then
keeps the answer for its own later reads. One process may serve several
callers whose keys differ -- a gateway, say, that launched with one user's
``.env`` -- so the switch lives in a context variable, never in ``os.environ``
or a module global another router would see.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_READS_ENVIRONMENT: ContextVar[bool] = ContextVar("web_router_reads_environment", default=True)


def reads_environment() -> bool:
    """Whether code running now may read a backend's key or setting from the environment."""
    return _READS_ENVIRONMENT.get()


@contextmanager
def environment(enabled: bool) -> Iterator[None]:
    """Read the environment, or not, for the code run inside: per thread and per task, undone on leaving."""
    token = _READS_ENVIRONMENT.set(enabled)
    try:
        yield
    finally:
        _READS_ENVIRONMENT.reset(token)


def env_value(env_var: str) -> str:
    """``env_var``'s value, stripped; empty when it is unset, blank, or the environment is not read here."""
    return (os.environ.get(env_var) or "").strip() if reads_environment() else ""


def explicit_or_env(value: str | None, env_var: str) -> str:
    """The explicit value, else the one in ``env_var`` where the environment is read, stripped; else empty."""
    return (value or "").strip() or env_value(env_var)
