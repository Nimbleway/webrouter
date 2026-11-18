"""One tool call from the model to what it reads back: checked, run by the executor, then rendered or failed.

The sync and async executors share every step but the router call itself, so
a model reads the same thing whichever ran it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from web_router.exceptions_typed import AllProvidersFailedError, ExtractFailedError
from web_router.integrations._input import checked, screened, unknown_tool
from web_router.integrations._messages import MSG_DID_NOT_RUN, MSG_FAILED, MSG_NO_URL_ALLOWED
from web_router.integrations._render import (
    failure_row,
    render_answer,
    render_extract,
    render_extract_failure,
    render_search,
)
from web_router.integrations._specs import TOOLS
from web_router.integrations.errors import TOOL_FAILURES, short_error
from web_router.routing.merge_urls import UrlCheck
from web_router.types import ExtractFailure, ExtractResponse, SearchResponse
from web_router.types_answer import AnswerResponse


@dataclass(frozen=True)
class Planned:
    """A call that passed every check: the tool, its method, and what to send."""

    name: str
    method: str
    query: str = ""
    urls: list[str] = field(default_factory=list)
    """The URLs the URL policy admitted, in the model's order."""
    refused: list[dict[str, Any]] = field(default_factory=list)
    """The URLs it refused, as the failure rows the model reads."""

    @property
    def target(self) -> str | list[str]:
        """What the router method takes first: the query, or the URLs."""
        return self.urls if self.method == "extract" else self.query


def plan(name: str, tool_input: object, methods: Sequence[str], check: UrlCheck) -> Planned | str:
    """The call to run, or what the model reads when it does not run: a wrong name, input or every URL refused."""
    method = next((m for m in methods if TOOLS[m].name == name), None)
    if method is None:
        return MSG_DID_NOT_RUN.format(tool=name, why=unknown_tool(name, methods))
    arguments, why = checked(method, tool_input)
    if why is not None:
        return MSG_DID_NOT_RUN.format(tool=name, why=why)
    if method != "extract":
        return Planned(name, method, query=arguments["query"])
    return planned_extract(name, arguments["urls"], check)


def planned_extract(name: str, urls: list[str], check: UrlCheck) -> Planned | str:
    """An extract of the URLs ``check`` admits, or what the model reads when it refused every one."""
    kept, refused = screened(urls, check)
    if not kept:
        return render_extract_failure(MSG_DID_NOT_RUN.format(tool=name, why=MSG_NO_URL_ALLOWED), refused)
    return Planned(name, "extract", urls=kept, refused=refused)


def rendered(
    planned: Planned,
    response: SearchResponse | ExtractResponse | AnswerResponse,
    result_chars: int,
    document_chars: int,
) -> str:
    """What the model reads of a call that answered."""
    if isinstance(response, SearchResponse):
        return render_search(response, result_chars)
    if isinstance(response, AnswerResponse):
        return render_answer(response, result_chars)
    return render_extract(response, document_chars, planned.refused)


def failed(planned: Planned, exc: BaseException, result_chars: int) -> str:
    """What the model reads of a call that failed: one short line, and for an extract each URL as a partial one has.

    Each URL keeps its own kind where the error recorded one (:func:`_page_failures`); the rest take the call's.
    """
    error = failed_text(planned.name, exc, result_chars)
    if planned.method != "extract":
        return error
    own = _page_failures(exc, planned.urls)
    kind, status = getattr(exc, "kind", None), getattr(exc, "status_code", None)
    rows = [
        failure_row(url, own[url].kind, own[url].retryable, own[url].status_code)
        if url in own
        else failure_row(url, kind, getattr(exc, "retryable", None), status)
        for url in planned.urls
    ]
    return render_extract_failure(error, [*rows, *planned.refused])


def _page_failures(exc: BaseException, urls: list[str]) -> dict[str, ExtractFailure]:
    """Each URL's own failure: an extract's, else the failed call's for every URL it carried.

    Under ``auto`` every member was sent the same URLs, so each URL keeps the latest tried member's failure, as a
    partly successful ``auto`` extract reports it; a member that could not be tried at all says nothing about one.
    """
    if isinstance(exc, ExtractFailedError):
        return {failure.url: failure for failure in exc.url_failures}
    if isinstance(exc, AllProvidersFailedError):
        found: dict[str, ExtractFailure] = {}
        for member in exc.failures.values():
            found.update(_page_failures(member, urls))
        return found
    return {url: ExtractFailure.from_error(url, exc) for url in urls} if isinstance(exc, TOOL_FAILURES) else {}


def failed_text(name: str, exc: BaseException, result_chars: int) -> str:
    """The one line a failed call's model reads: ``<tool> failed:`` and :func:`short_error`."""
    return MSG_FAILED.format(tool=name, why=short_error(exc, limit=result_chars))
