"""Running a router in production: its logger, the cooldown after a throttled backend, and its messages.

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

MSG_COOLING_DOWN: Final = (
    "cooling down after a {status} for {seconds:.1f}s more; auto passed it over without calling it"
)
MSG_INVALID_TIMEOUT: Final = "timeout takes a finite number of seconds, 0 or more, or None for no limit; got {value!r}"
MSG_INVALID_MAX_CONCURRENCY: Final = "max_concurrency must be a whole number of 1 or more, or None; got {value!r}"

LOG_HOP_OK: Final = "auto %s: %s answered in %.3fs%s"
LOG_HOP_FAILED: Final = "auto %s: %s failed (%s, status %s) in %.3fs%s"
LOG_HOP_COOLING: Final = "auto %s: %s skipped, cooling down for %.1fs more"
LOG_COOLDOWN_SET: Final = "%s cools down for %.1fs after a %s"
LOG_WARMUP_FAILED: Final = "warmup: %s could not build its client (%s); its first call will try again"
LOG_HOST_SUFFIX: Final = " for %s"
LOG_MORE_HOSTS: Final = "%s and %d more"

MSG_SYNC_ROUTER_IN_LOOP: Final = (
    "WebRouter.{method}() was called inside a running event loop, which it blocks until the call returns. "
    "Use AsyncWebRouter and await it there, or run the call on a worker thread (asyncio.to_thread)."
)
