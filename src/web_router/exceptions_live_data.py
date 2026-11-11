"""The errors live data raises: a source name the backend does not have, and a billed job that did not finish.

Kept apart from :mod:`web_router.exceptions`, which is at its size limit, as
:mod:`web_router.exceptions_budget` is; it imports that module, never the reverse.
"""

from __future__ import annotations

from web_router.constants_live_data import MSG_LIVE_DATA_SOURCE_NOT_FOUND
from web_router.exceptions import ProviderError
from web_router.live_data_compat import deprecated_keyword


class LiveDataSourceNotFoundError(ProviderError):
    """A live data run or describe named a source the backend does not have.

    A :class:`ProviderError`, because the backend answered and said so, with
    its own class so a caller can tell a wrong name from a run that failed.
    Source names are the vendor's own: the same site has a different name on
    each. ``hint``, when a backend has one, is appended to the message: names
    close to the one asked for. :attr:`template` is the deprecated name of
    :attr:`source`, and so is the ``template=`` keyword.
    """

    def __init__(
        self,
        provider: str,
        source: str | None = None,
        *,
        original: BaseException | None = None,
        hint: str = "",
        **old: str,
    ) -> None:
        source = deprecated_keyword(type(self).__name__, old, "template", "source", source)
        self.source = source
        self.template = source
        super().__init__(provider, MSG_LIVE_DATA_SOURCE_NOT_FOUND.format(name=source) + hint, original=original)


class LiveDataJobError(ProviderError):
    """A live data run the backend took as a job failed, or was still running, after it was submitted.

    The job is billed once submitted, so :attr:`job_id` names it on every
    error after that point -- a faulted job, a poll that failed, or a wait cut
    short by the call's deadline -- and the job can be read later rather than
    paid for again.
    """

    def __init__(self, provider: str, message: str, *, job_id: str, original: BaseException | None = None) -> None:
        super().__init__(provider, message, original=original)
        self.job_id = job_id
