"""The wording of a deadline error."""

from __future__ import annotations

from web_router.exceptions import DeadlineExceededError


def test_a_failure_ending_in_a_period_is_not_given_a_second_one() -> None:
    message = str(DeadlineExceededError("auto", 5, tried="tavily: timed out."))
    assert message.endswith("Before it ran out: tavily: timed out.")
