"""A decider's reply, held to the question it answers before anything in it is used.

Experimental. Every id must be a ``str`` the question offered, each once; every
score and the confidence a finite number from 0 to 1 that is not a bool. A reply
that breaks any of it is set aside whole: the call runs on the static order and
the decision record says ``router_invalid``. No message echoes the reply.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TypeGuard

from web_router.constants_decider import (
    MSG_DECIDER_CONFIDENCE,
    MSG_DECIDER_RANKED,
    MSG_DECIDER_REPLY,
    MSG_DECIDER_SCORES,
)
from web_router.exceptions import WebRouterError
from web_router.routing._runner import InvalidAnswer
from web_router.routing.decider import DeciderReply


class DeciderReplyError(WebRouterError, InvalidAnswer):
    """A decider's reply does not answer its question: recorded as ``router_invalid``."""


def unit_number(value: object) -> TypeGuard[int | float]:
    """A finite number from 0 to 1, not a bool."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    try:
        return math.isfinite(float(value)) and 0 <= value <= 1
    except OverflowError:  # an int too large for a float
        return False


def checked_reply(reply: object, options: Mapping[str, str], name: str) -> DeciderReply:
    """``reply`` once its ranking, scores and confidence fit the question's ``options``.

    Raises:
        DeciderReplyError: for anything but a :class:`DeciderReply`, a ranking that is not a list or tuple of
            ``str`` ids offered, each once, scores that are not a mapping of ranked ids to numbers from 0 to 1,
            or a confidence that is not such a number.
    """
    if not isinstance(reply, DeciderReply):
        raise DeciderReplyError(MSG_DECIDER_REPLY.format(name=name))
    ranked = reply.ranked
    if (
        not isinstance(ranked, list | tuple)
        or not all(type(cid) is str and cid in options for cid in ranked)
        or len(set(ranked)) != len(ranked)
    ):
        raise DeciderReplyError(MSG_DECIDER_RANKED.format(name=name))
    scores = reply.scores
    if scores is not None and not (
        isinstance(scores, Mapping)
        and all(type(cid) is str and cid in ranked and unit_number(p) for cid, p in scores.items())
    ):
        raise DeciderReplyError(MSG_DECIDER_SCORES.format(name=name))
    if reply.confidence is not None and not unit_number(reply.confidence):
        raise DeciderReplyError(MSG_DECIDER_CONFIDENCE.format(name=name))
    return reply
