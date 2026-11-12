"""``log_spend()``: where a router writes each call's cost and time. Apart so ``client.py`` stops growing."""

from __future__ import annotations

from pathlib import Path

from web_router.constants_spend import MSG_SPEND_SINK
from web_router.exceptions import ConfigurationError
from web_router.spend import SpendLog, SpendSink


class SpendLogging:
    _spend_sink: SpendSink | None = None

    def log_spend(self, to: str | Path | SpendSink | None, /) -> SpendSink | None:
        """Log every call this router answers from now on to ``to``: a file path, or any object with ``record``.

        A path is a :class:`~web_router.SpendLog`, appended to as JSON lines and read back with its
        :meth:`~web_router.SpendLog.report`; ``None`` stops logging. Returns the sink in force. Calls made through
        :meth:`budget`'s scope are this router's calls, and are logged too.

        Raises:
            ConfigurationError: for anything else.
        """
        if to is None or isinstance(to, SpendSink):
            sink = to
        elif isinstance(to, str | Path):
            sink = SpendLog(to)
        else:
            raise ConfigurationError(MSG_SPEND_SINK.format(kind=type(to).__name__))
        self._spend_sink = sink
        return sink
