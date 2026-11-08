"""What a one-call ``auto`` chain does when the call's deadline runs out, apart so ``_chain.py`` stops growing.

Search, extract and answer check before each member whether time is left;
once it is not, the chain returns the first empty answer it holds, else
raises the call's :class:`~web_router.exceptions.DeadlineExceededError`
naming each member's reason.
"""

from __future__ import annotations

from collections.abc import Mapping

from web_router.exceptions import DeadlineExceededError, WebRouterError
from web_router.providers._chain import _Answer
from web_router.providers._deadline import current_deadline
from web_router.routing.attempts import noted
from web_router.routing.contract import ToolName


def _out_of_time() -> bool:
    """Whether a deadline is in force and has passed, so no further member may start.

    Checked before each member of a one-call chain -- search, extract, answer.
    No time is held back for later members: each one runs under its own
    timeout clamped to whatever is left, so a member that hangs gives up at
    its own timeout, the next starts with what remains, and the last runs to
    the deadline itself. The billed chains never check it: a research or
    browse submit is clamped as on its own, never cut short for a member after it.
    """
    deadline = current_deadline()
    return deadline is not None and deadline.left() <= 0


def _late_answer(held: _Answer | None, provider: str, failures: dict[str, WebRouterError]) -> _Answer | None:
    """``None`` while time is left for the next member; once it has run out, the first empty answer, else the deadline.

    Checked at the head of each member of a one-call chain.
    """
    if not _out_of_time():
        return None
    return _held_or_late(held, provider, failures)


def _held_or_late(
    held: _Answer | None,
    provider: str,
    failures: dict[str, WebRouterError],
    late: DeadlineExceededError | None = None,
    member: str | None = None,
    tool: ToolName | None = None,
) -> _Answer:
    """The first empty answer when one came back before the deadline, else the deadline, naming what was tried.

    ``late`` is the deadline error ``member`` raised, when it was one that ran
    into it rather than the chain finding no time left before the next; it is
    reported to the call's failure hook as ``tool``'s.
    """
    if late is not None and member is not None and tool is not None:
        noted(tool, member, late)
    if held is not None:
        return held
    raise _deadline_error(provider, failures, late, member) from late


def _deadline_error(
    provider: str,
    failures: Mapping[str, WebRouterError],
    late: DeadlineExceededError | None = None,
    member: str | None = None,
) -> DeadlineExceededError:
    """The deadline of the call in flight, as a chain's error naming each member's reason."""
    tried: dict[str, WebRouterError] = dict(failures)
    if late is not None and member is not None:
        tried[member] = late
    deadline = current_deadline()
    seconds = late.seconds if late is not None else deadline.seconds if deadline is not None else 0.0
    detail = "; ".join(f"{name}: {exc}" for name, exc in tried.items()) or None
    return DeadlineExceededError(provider, seconds, tried=detail)
