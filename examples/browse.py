"""A browser-agent task: say what to do, and a vendor's agent does it.

`browse()` is the other half of the browser work. `open_browser()` gives you a
browser to drive; `browse()` hands the driving to a hosted agent -- Browser Use,
Skyvern or Browserbase -- which works through the task in one of its own
browsers and answers minutes later. It is research's shape: a job, not a call,
so this script shows both ways to hold one, blocking on it and submitting it.

Needs a key. Every task bills, so there is no keyless tier: with no credential
this names the variables that would fix it and exits without starting anything.

Run it with::

    make setup   # in your web-router-alpha clone: every extra
    uv run --env-file .env python examples/browse.py
"""

from __future__ import annotations

from web_router import (
    BrowseTimeoutError,
    MissingParameterError,
    UnsupportedParameterError,
    WebRouter,
    configured_browse_providers,
)
from web_router.constants import BROWSE_ENV_BY_PROVIDER

TASK = "Find the title of the top story and how many points it has."
START = "https://news.ycombinator.com"

USABLE = [name for name in configured_browse_providers() if name != "auto"]


def blocking(router: WebRouter) -> bool:
    """The simplest form: a task in, an answer and the steps behind it out."""
    print(f"browsing: {TASK}\n(this takes minutes, not seconds)")

    try:
        # `auto` passes over a member that cannot take a start URL -- Browserbase's
        # Agents API has none -- before anything is billed, and says why when
        # no member is left.
        response = router.browse(TASK, start_url=START)
    except (MissingParameterError, UnsupportedParameterError) as exc:
        print(f"no configured backend can run this task as asked:\n  {exc}")
        return False

    print(f"\n{response.provider} answered, {response.outcome}")
    print(response.answer or response.structured)
    for step in response.steps[:5]:
        print(f"  - {step.action or '(step)'} {step.url or ''}")
    return True


def without_waiting(router: WebRouter) -> None:
    """Submit the task, come back for it. Giving up on the wait does not cancel it."""
    job = router.submit_browse(TASK, start_url=START)
    print(f"\nsubmitted {job.job_id} to {job.provider}; state is {job.state}")

    try:
        response = job.result(timeout=30)
    except BrowseTimeoutError as exc:
        print(f"still running after {exc.timeout:g}s -- polling again would pick it back up")
        return

    print(f"finished after all, in {len(response.steps)} steps")


def main() -> None:
    if not USABLE:
        variables = ", ".join(var for needed in BROWSE_ENV_BY_PROVIDER.values() for var in needed)
        print(f"No browse backend is configured. Set one of {variables} and run this again.")
        return

    print(f"browse backends configured: {', '.join(USABLE)}")
    with WebRouter() as router:
        if blocking(router):
            without_waiting(router)


if __name__ == "__main__":
    main()
