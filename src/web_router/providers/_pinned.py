"""What the router pins on a job or a browser session when it creates one: its presets, and a job's decision.

Apart from :mod:`web_router.providers.base` so that file stops growing.
:class:`~web_router.providers.base.Job` and the browser handles take these as
mixins; the routers pin what :mod:`web_router.presets` resolved, and every read
hands out a fresh copy, so changing what one read returned cannot change the next.
"""

from __future__ import annotations

from typing import Any, TypeVar, cast

from pydantic import BaseModel

from web_router.types_meta import ResponseMeta

_T = TypeVar("_T")


class PinnedMeta:
    """A pinned :class:`~web_router.ResponseMeta`, read back as a copy with its checks ``passed``."""

    _meta: ResponseMeta | None = None

    def _pin(self, meta: ResponseMeta | None) -> None:
        self._meta = meta

    @property
    def meta(self) -> ResponseMeta | None:
        """A copy of the presets this was opened under, pinned when it opened; ``None`` when none applied."""
        return None if self._meta is None else self._meta.at("passed")


class PinnedJobMeta(PinnedMeta):
    """A job's pinned presets: ``pending`` until the run completes, and stamped on its answer however it is read."""

    decision: Any = None
    """The answer's ``decision``: under a budget, the effort it mapped and what it charged the start."""

    @property
    def meta(self) -> ResponseMeta | None:
        """The presets this run was started under, pinned when it was submitted; ``None`` when none applied.

        Their checks read ``pending`` until the run has completed and ``passed``
        after: a run the backend accepted is not yet a run that finished.
        """
        if self._meta is None:
            return None
        # Job holds `_progress`, typed there; read through Any so this mixin does not redeclare it.
        return self._meta.at("passed" if cast(Any, self)._progress.state == "completed" else "pending")

    def _stamped(self, progress: _T) -> _T:
        """A copy of ``progress`` with the pinned metadata on its answer, so a caller polling sees it too.

        Stamped on every read rather than once, so changing what one read
        returned cannot change the next.
        """
        response = getattr(progress, "response", None)
        if self._meta is None or response is None or not isinstance(progress, BaseModel):
            return progress
        return progress.model_copy(update={"response": self._with_meta(response)})

    def _with_meta(self, response: _T) -> _T:
        """``response`` with the pinned metadata, its checks ``passed``; as it is when nothing was pinned."""
        if self._meta is None:
            return response
        return cast("_T", cast(Any, response).model_copy(update={"meta": self._meta.at("passed")}))
