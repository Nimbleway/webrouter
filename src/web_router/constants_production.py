"""Running a router in production: its logger, the cooldowns after a throttled or timed-out backend, and its messages.

Kept apart so ``constants.py`` stops growing.
"""

from __future__ import annotations

from typing import Final

LOGGER_NAME: Final = "web_router"
"""The package's logger; every module logs under it, and it carries only a ``NullHandler`` until the caller adds one."""

COOLDOWN_STATUSES: Final = frozenset({402, 429})
"""The statuses after which ``auto`` passes a backend over for a while: throttled, or out of quota."""

COOLDOWN_DEFAULT_S: Final[dict[int, float]] = {429: 10.0, 402: 60.0}
"""How long a backend cools down after a status that came without a ``Retry-After``. A 402 is a quota spent,
which takes longer to come back than a burst does."""

COOLDOWN_FALLBACK_S: Final = 10.0
"""The cooldown for a ``rate_limit`` failure with neither a status nor a ``Retry-After``."""

COOLDOWN_MAX_S: Final = 600.0
"""The longest a backend cools down, whatever its ``Retry-After`` says: a router outlives one burst."""

TIMEOUT_COOLDOWN_AFTER: Final = 3
"""With ``timeout_cooldown`` on (the default), the timeouts in a row after which ``auto`` passes a backend over."""

TIMEOUT_COOLDOWN_S: Final = 30.0
"""How long a backend cools down after :data:`TIMEOUT_COOLDOWN_AFTER` timeouts in a row."""

COOLDOWN_STATUS: Final = "a {status}"
"""What a cooldown after a throttle names as its cause: the status, or ``rate_limit`` with none."""

COOLDOWN_TIMEOUTS: Final = "{count} timeouts in a row"
"""What a cooldown after timeouts names as its cause, where a throttle's names its status."""

HOP_CODE_COOLING_DOWN: Final = "cooling_down"
"""What a decision record's hop says for a backend ``auto`` passed over while it cooled down."""
MSG_COOLING_DOWN: Final = "cooling down after {cause} for {seconds:.1f}s more; auto passed it over without calling it"
MSG_INVALID_TIMEOUT: Final = "timeout takes a finite number of seconds, 0 or more, or None for no limit; got {value!r}"
HOP_SHARE: Final = 0.5
"""With no ``hop_timeout`` set, the share of a call's time left that each ``auto`` hop but the last may take."""

MSG_INVALID_HOP_TIMEOUT: Final = (
    "hop_timeout takes seconds above 0, None for a share of the time left, or False for no cap; got {value!r}"
)
MSG_HOP_TIMED_OUT: Final = "no answer within its hop timeout of {seconds:.1f}s; auto moved on to the next backend"
MSG_INVALID_MAX_CONCURRENCY: Final = "max_concurrency must be a whole number of 1 or more, or None; got {value!r}"

LOG_HOP_OK: Final = "auto %s: %s answered in %.3fs%s"
LOG_HOP_CANCELLED: Final = "cancelled"
LOG_HOP_CUT_OFF: Final = "cut off at the deadline"
LOG_HOP_FAILED: Final = "auto %s: %s failed (%s, status %s) in %.3fs%s"
LOG_HOP_COOLING: Final = "auto %s: %s skipped, cooling down for %.1fs more"
LOG_COOLDOWN_SET: Final = "%s cools down for %.1fs after %s"
LOG_WARMUP_FAILED: Final = "warmup: %s could not build its client (%s); its first call will try again"
LOG_HOST_SUFFIX: Final = " for %s"
LOG_MORE_HOSTS: Final = "%s and %d more"

MSG_SYNC_ROUTER_IN_LOOP: Final = (
    "WebRouter.{method}() was called inside a running event loop, which it blocks until the call returns. "
    "Use AsyncWebRouter and await it there, or run the call on a worker thread (asyncio.to_thread)."
)

MSG_BODY_PAST_DEADLINE: Final = "the response body was still arriving when the call's deadline passed"
"""The read timeout a sync HTTP call raises when its body is still arriving at the deadline."""
