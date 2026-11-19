"""The scans' judge: ``--judge`` names it, and it grades one known answer before anything is paid for.

``Judge.check()`` catches a missing SDK or key; only a real call catches an
expired key, a retired model or a parameter the model refuses. Without it a
scan searches every question and only then finds every row failed at the judge.
"""

from __future__ import annotations

import argparse
from typing import Any

from web_router.constants_evals import EVALS_DEFAULT_JUDGE_SPEC, EVALS_JUDGE_SPEC_HELP
from web_router.evals import Judge, judge_from

JUDGE_PROBE = ("What is 2 + 2?", "4", "4")
"""One question the judge must grade before any backend is asked, so a judge that cannot run costs no search."""
MSG_JUDGE_FAILED = "The judge could not grade a test answer, so no backend was asked: {error}"


async def check_judge(judge: Any) -> None:
    """Check the judge's setup, then grade one known answer; any failure closes it and stops the scan."""
    try:
        judge.check()
        await judge.agrade(*JUDGE_PROBE)
    except Exception as exc:  # whatever the judge's SDK raises, the scan must stop on it
        await judge.aclose()
        raise SystemExit(MSG_JUDGE_FAILED.format(error=exc)) from exc


def judge_arg(parser: argparse.ArgumentParser) -> None:
    """``--judge``: a spec ``judge_from()`` takes, ``openai:gpt-4o`` unless named."""
    parser.add_argument(
        "--judge", default=EVALS_DEFAULT_JUDGE_SPEC, help=EVALS_JUDGE_SPEC_HELP.format(default=EVALS_DEFAULT_JUDGE_SPEC)
    )


async def checked_judge(spec: str) -> Judge:
    """The judge ``spec`` names, once it has graded a known answer; a scan stops here when it cannot."""
    judge = judge_from(spec)
    await check_judge(judge)
    return judge
