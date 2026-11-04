"""Helpers shared by provider implementations."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from web_router.constants import (
    BROWSER_FIELD_CASES,
    BROWSER_SECONDS_PARAMETERS,
    MIN_CALL_BUDGET,
    MIN_JOB_BUDGET,
    MSG_BROWSER_FIELD_CASE_NOT_TEXT,
    MSG_BROWSER_FIELD_COLLISION,
    MSG_BROWSER_NO_FIELD_FOR_VALUE,
    MSG_BROWSER_OPTION_CONFLICT,
    MSG_BROWSER_OPTION_CONFLICT_ASIDES,
    MSG_OPTION_VALUE,
    SNIPPET_LIMIT,
)
from web_router.exceptions import ConfigurationError
from web_router.types import (
    REQUEST_VALUE,
    BrowserField,
    BrowserFieldRow,
    BrowserRequest,
    FieldPath,
    NormalizedRequest,
)


def resolve_api_key(api_key: str | None, *, env_var: str, provider: str) -> str:
    """Return the explicit key, else the one in ``env_var``, stripped of surrounding whitespace.

    Raises:
        ConfigurationError: if neither is available, naming the variable to set.
            An empty or whitespace-only value counts as missing.
    """
    key = (api_key or "").strip() or (os.environ.get(env_var) or "").strip()
    if not key:
        raise ConfigurationError.missing_api_key(provider, env_var)
    return key


def resolve_setting(value: str | None, *, env_var: str, provider: str, setting: str) -> str:
    """Return the explicit value of a required non-credential setting, else the one in ``env_var``, stripped.

    Raises:
        ConfigurationError: if neither is available, naming the variable to set.
            An empty or whitespace-only value counts as missing.
    """
    resolved = (value or "").strip() or (os.environ.get(env_var) or "").strip()
    if not resolved:
        raise ConfigurationError.missing_setting(provider, env_var=env_var, setting=setting)
    return resolved


def build_payload(
    request: NormalizedRequest,
    names: Mapping[str, str] | None = None,
    /,
    **fixed: Any,
) -> dict[str, Any]:
    """Assemble a provider payload from the options it maps.

    Args:
        request: The normalized request.
        names: Normalized parameter -> this provider's field name. Omitted by a
            provider whose every option maps onto a table rather than a rename,
            which is every extract backend so far. Positional-only, so a payload
            field of any type can be passed as a keyword without colliding
            with it.
        **fixed: Fields the provider always sends, such as the query itself, or
            ones it derives (a depth or format mapping, say). ``None`` values are
            dropped, so a derived field that does not apply can be passed
            unconditionally.

    Every search, extract and research provider builds its payload through
    here, which is what guarantees two things a new provider would otherwise
    have to remember: unset options are never sent, and ``provider_options``
    always reaches the API last so it can override anything above it. A browser
    provider uses :func:`build_browser_payload` instead, where
    ``provider_options`` may not override a mapped option.
    """
    mapped = {theirs: getattr(request, ours) for ours, theirs in (names or {}).items()}
    payload = compact({**fixed, **mapped})
    payload.update(request.provider_options)
    return payload


def build_browser_payload(
    request: BrowserRequest,
    fields: Mapping[str, BrowserFieldRow],
    convert: Callable[[float], Any],
) -> dict[str, Any]:
    """Assemble the body that opens a browser session.

    Args:
        request: The normalized request, its default timeout already filled in.
        fields: Normalized option -> its row: the path of keys it goes under in
            this API's body, a lookup from its value to the fields that value
            sets, or a :class:`~web_router.types.BrowserField` for an option
            that sets several fields at once. :class:`~web_router.types.BrowserField`
            describes all three.
        convert: Turns a duration, in our seconds, into the unit this API
            takes. Applied to :data:`~web_router.constants.BROWSER_SECONDS_PARAMETERS`
            alone, so a flag or a country code is never passed through it.

    Unset options are not sent, as with :func:`build_payload`. What differs is
    ``provider_options``: it is merged underneath the mapped options, at every
    depth, and one that sets a mapped field raises rather than winning. A
    session's lifetime is filled in from ``BROWSER_TIMEOUT`` when the caller
    sets none, so letting either side win would silently drop the other --
    and a shallow merge would let ``{"session": {...}}`` wipe a nested
    lifetime the caller never saw. A ``None`` on the way to a mapped field
    (``{"session": None}``) counts as empty, and any mapping is read as one.
    Every field a row produces is held to this, however many one row sets, and
    a produced value is placed whole: a list is a leaf, so one that
    ``provider_options`` also sets raises rather than being merged or appended
    to. The caller's ``provider_options`` and the table are never mutated.

    Raises:
        ConfigurationError: if ``provider_options`` sets a field a mapped option
            also sets. Also, each naming the table as the fault rather than the
            caller: if two fields the table produces collide, if a lookup row
            does not list the request's value -- a provider that accepted a
            value its own table cannot send -- or if a row sets a case for a
            value that is not text.
    """
    payload = dict(request.provider_options)
    placed: dict[FieldPath, str] = {}
    for parameter, row in fields.items():
        value = getattr(request, parameter)
        if value is None:
            continue
        if parameter in BROWSER_SECONDS_PARAMETERS:
            value = convert(value)
        elif isinstance(value, BaseModel):
            # A `Viewport` is sent as its fields, which is what a JSON body can carry.
            value = value.model_dump()
        for path, produced in _produced(parameter, row, value).items():
            _check_collision(placed, path, parameter)
            _place(payload, path, produced, parameter)
            placed[path] = parameter
    return payload


def _produced(parameter: str, row: BrowserFieldRow, value: Any) -> Mapping[FieldPath, Any]:
    """Every field one option's row sets for this value, path -> what goes there."""
    if isinstance(row, BrowserField):
        if row.case is not None:
            if not isinstance(value, str):
                raise ConfigurationError(MSG_BROWSER_FIELD_CASE_NOT_TEXT.format(parameter=parameter, value=value))
            value = BROWSER_FIELD_CASES[row.case](value)
        return {path: _filled(template, value) for path, template in row.sets.items()}
    if isinstance(row, tuple):
        return {row: value}
    if value not in row:
        listed = ", ".join(MSG_OPTION_VALUE.format(name=parameter, value=known) for known in row)
        named = MSG_OPTION_VALUE.format(name=parameter, value=value)
        raise ConfigurationError(MSG_BROWSER_NO_FIELD_FOR_VALUE.format(value=named, listed=listed))
    return {path: _filled(template, value) for path, template in row[value].items()}


def _filled(template: Any, value: Any) -> Any:
    """``template`` with the request's value wherever :data:`REQUEST_VALUE` stands, rebuilt rather than shared."""
    if template is REQUEST_VALUE:
        return value
    if isinstance(template, Mapping):
        return {key: _filled(item, value) for key, item in template.items()}
    if isinstance(template, list | tuple):
        return type(template)(_filled(item, value) for item in template)
    return template


def _check_collision(placed: Mapping[FieldPath, str], path: FieldPath, parameter: str) -> None:
    """Raise when ``path`` is, or sits inside or around, a field the table already produced.

    Checked apart from ``provider_options``, which :func:`_place` checks: two
    rows meeting is the table's fault, and blaming the caller's options for it
    would send them looking for a clash they did not make.
    """
    for other, other_parameter in placed.items():
        if path[: len(other)] == other or other[: len(path)] == path:
            raise ConfigurationError(
                MSG_BROWSER_FIELD_COLLISION.format(
                    parameter=parameter,
                    field=".".join(path),
                    other=other_parameter,
                    other_field=".".join(other),
                )
            )


def _place(payload: dict[str, Any], path: FieldPath, value: Any, parameter: str) -> None:
    node = payload
    for depth, key in enumerate(path[:-1]):
        child = node.get(key)
        if child is None:
            child = {}
        elif not isinstance(child, Mapping):
            raise _option_conflict(path[: depth + 1], parameter)
        child = dict(child)
        node[key] = child
        node = child
    if path[-1] in node:
        raise _option_conflict(path, parameter)
    node[path[-1]] = value


def _option_conflict(path: FieldPath, parameter: str) -> ConfigurationError:
    aside = MSG_BROWSER_OPTION_CONFLICT_ASIDES.get(parameter, "")
    return ConfigurationError(
        MSG_BROWSER_OPTION_CONFLICT.format(field=".".join(path), parameter=parameter, aside=aside)
    )


def model_to_dict(model: Any) -> dict[str, Any]:
    """Convert an SDK response model to a plain dict, across pydantic v1 and v2."""
    for method in ("model_dump", "dict"):
        dump = getattr(model, method, None)
        if callable(dump):
            result: dict[str, Any] = dump()
            return result
    return dict(model)


def error_detail(error: Any) -> str | None:
    """A job's error as the reason a person reads: its ``message`` when the API sent an error object."""
    if error is None:
        return None
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or error)
    return str(error)


def compact(mapping: dict[str, Any]) -> dict[str, Any]:
    """Drop ``None`` values so unset parameters never reach the provider."""
    return {key: value for key, value in mapping.items() if value is not None}


def best_text(body: str | None, description: str | None) -> str:
    """Pick the fullest text a result offers.

    Providers disagree about where the body lands: Nimble returns an enriched
    ``description`` and an empty ``content`` at mid depths, Tavily separates
    ``content`` from ``raw_content``, You.com nests it under ``contents``.
    Normalizing here keeps ``SearchResult.content`` meaning the same thing
    whichever backend served the query.
    """
    return body or description or ""


def as_snippet(text: str) -> str:
    """Shorten body text to something the size of a snippet.

    For providers that return page content but no summary, so ``description``
    means the same thing everywhere. Breaks on whitespace to avoid cutting a
    word in half.
    """
    if len(text) <= SNIPPET_LIMIT:
        return text
    head = text[:SNIPPET_LIMIT]
    cut = head.rfind(" ")
    return (head[:cut] if cut > 0 else head).rstrip() + "\u2026"


@dataclass(frozen=True)
class Deadline:
    """The instant the call in flight must answer by, and the budget that set it.

    ``at`` is on :func:`time.monotonic`'s clock; ``seconds`` is what the budget
    was when it was set, kept so an error can name the deadline the caller
    gave rather than whatever fraction of it was left.
    """

    at: float
    seconds: float

    def left(self) -> float:
        """Seconds until :attr:`at`; zero or less once it has passed."""
        return self.at - time.monotonic()


_DEADLINE: ContextVar[Deadline | None] = ContextVar("web_router_deadline", default=None)
"""When the call currently in flight -- a search, an extract, a research run -- must be given up on.

A context variable rather than a parameter on ``_search``, ``_start`` and the
rest, which is the one place this package reaches for something implicit. The
reason is that the budget is not a property of the *request* -- two callers
can wait different lengths for the same question -- and it has to reach the
HTTP and SDK calls at the bottom without appearing in the signature of every
provider between here and there. Being a ``ContextVar`` rather than an
attribute is what makes it correct under concurrency: one router shared by
several tasks gives each of them its own budget, and an async task inherits
the one belonging to whoever awaited it. A worker thread does not: whoever
hands a call to one runs it in :func:`contextvars.copy_context`.
"""


@contextmanager
def call_deadline(seconds: float | None) -> Iterator[None]:
    """Give every provider call made inside this block at most ``seconds`` from now.

    The one way in for a per-call time limit on any method; experimental, as
    everything is before 1.0. ``None`` adds no limit. A block inside another
    can only tighten the deadline, never extend it: the earlier of the two
    holds, and an error names the budget that set it.

    What happens at the deadline depends on the method. A search, extract,
    template run or answer raises
    :class:`~web_router.exceptions.DeadlineExceededError`; an extract keeps
    the documents that came back in time and reports the URLs that did not as
    failures. ``auto`` tries a next member only while time is left. Research
    and browse wait no longer than the deadline and raise their own timeout
    error, which carries the job id; the call that submits a billed run is
    clamped as any other, and nothing is cut short to leave time for another
    member.
    """
    with _tightened(None if seconds is None else Deadline(at=time.monotonic() + seconds, seconds=seconds)):
        yield


@contextmanager
def job_deadline(deadline: float | None) -> Iterator[None]:
    """Apply the monotonic instant ``deadline`` to every provider call made inside this block.

    As :func:`call_deadline`, for a caller that already holds the instant --
    the job core, which measures its own wait from the same one.
    """
    with _tightened(None if deadline is None else Deadline(at=deadline, seconds=max(0.0, deadline - time.monotonic()))):
        yield


@contextmanager
def _tightened(deadline: Deadline | None) -> Iterator[None]:
    """Hold ``deadline`` for this block, unless the one already in force comes first."""
    current = _DEADLINE.get()
    if deadline is not None and (current is None or deadline.at < current.at):
        current = deadline
    token = _DEADLINE.set(current)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def current_deadline() -> Deadline | None:
    """The deadline in force for the call in flight, or ``None`` when it has none."""
    return _DEADLINE.get()


def job_budget(default: float) -> float:
    """How long a single call may take: the caller's remaining time, or ``default``.

    Never longer than ``default``, so a generous job timeout cannot stretch
    one request beyond what the provider's own timeout allows, and never longer
    than what the caller has left, so a backend that does the whole run inside
    one request -- Perplexity's deep research model -- cannot hold a caller who
    asked for thirty seconds for the two minutes its HTTP timeout permits.

    Floored rather than allowed to reach zero: the waiting loop checks the
    deadline before it polls, so a call reaching here has time left, and a
    timeout of zero means "no timeout" to both httpx and the SDKs -- the exact
    opposite of what is being asked for.
    """
    deadline = _DEADLINE.get()
    if deadline is None:
        return default
    return max(MIN_JOB_BUDGET, min(default, deadline.left()))


def call_budget(default: float) -> float:
    """How long one request of a one-call method may take: ``default``, or less when a deadline leaves less.

    Never more than ``default`` and never more than the time left. Unlike
    :func:`job_budget`, floored only at
    :data:`~web_router.constants.MIN_CALL_BUDGET`, so a transport is never
    handed zero or less, yet never given time past the deadline; a call with
    no time left is refused before it is sent.
    """
    deadline = _DEADLINE.get()
    if deadline is None:
        return default
    return max(MIN_CALL_BUDGET, min(default, deadline.left()))


# The names the research providers were written against. Aliases rather than
# wrappers, so there is one budget whichever name a provider reads it by.
research_deadline = job_deadline
research_budget = job_budget
