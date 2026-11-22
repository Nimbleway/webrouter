"""The HTTP base classes for the job methods: research, polled research and browse.

Re-exported by :mod:`web_router.providers._http`, where a provider imports them from.
"""

from __future__ import annotations

import abc
from collections.abc import Mapping
from typing import Any, ClassVar
from urllib.parse import quote

import httpx

from web_router.constants import BROWSE_HTTP_TIMEOUT, RESEARCH_HTTP_TIMEOUT
from web_router.providers._deadline import job_budget, research_budget
from web_router.providers._http_base import _HttpProvider
from web_router.providers._utils import error_detail
from web_router.providers.base import BrowseProvider, ResearchProvider
from web_router.types import ResearchProgress, ResearchRequest, ResearchResponse, ResearchState


class HttpResearchProvider(_HttpProvider, ResearchProvider):
    """A research backend over a plain HTTP endpoint.

    Thinner than its two siblings on purpose. A search or an extract is one
    request with one shape, so those base classes can own the whole call; a
    research run is two or three requests whose shapes disagree between
    backends -- the result beside the status or behind another GET. What is
    shared here is what every one of them shares: the client, the credential,
    redirect handling, HTTP error mapping and a longer default timeout. The
    common shape, a POST that starts the run and a GET whose answer carries
    the result, is :class:`HttpPolledResearchProvider`.
    """

    def __init__(self, *, timeout: float = RESEARCH_HTTP_TIMEOUT, **kwargs: Any) -> None:
        """
        Args:
            timeout: Request timeout in seconds. Longer than the default for the
                other two methods, because on a backend that answers
                synchronously this one request *is* the run.
            **kwargs: As :class:`_HttpProvider`.
        """
        super().__init__(timeout=timeout, **kwargs)
        # Kept beside the client's own: every request clamps down from here to whatever the caller has left, never up.
        self._research_timeout = timeout

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._payload(self._client.post(path, json=payload, timeout=self._budget()))

    async def _apost(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._payload(await self._async_client.post(path, json=payload, timeout=self._budget()))

    def _get(self, path: str) -> dict[str, Any]:
        return self._payload(self._client.get(path, timeout=self._budget()))

    async def _aget(self, path: str) -> dict[str, Any]:
        return self._payload(await self._async_client.get(path, timeout=self._budget()))

    def _budget(self) -> float:
        """This request's timeout: the caller's remaining research budget, when there is one.

        Without it the client timeout is the only bound, and a backend that
        runs the whole job inside one request would hold a caller who asked for
        thirty seconds for the two minutes that timeout allows.
        """
        return research_budget(self._research_timeout)


class HttpPolledResearchProvider(HttpResearchProvider):
    """A research backend that starts a run with a POST and reports it, answer included, with a GET.

    The subclass declares where the two calls go, its status table and where
    its job id sits, and implements :meth:`_build_payload` and
    :meth:`_to_response`. A backend that reads its answer from a third call
    implements the job methods itself, against :class:`HttpResearchProvider`.
    """

    research_path: ClassVar[str]

    status_path: ClassVar[str]
    """Where a run is reported, with ``{job_id}`` where its id goes."""

    research_states: ClassVar[Mapping[str, ResearchState]]

    job_id_fields: ClassVar[tuple[str, ...]] = ("id",)
    """Where a run's id may sit, in order: some backends name it one way when starting and another when reporting."""

    @abc.abstractmethod
    def _build_payload(self, request: ResearchRequest) -> dict[str, Any]:
        """Translate a normalized request into the body that starts a run."""

    @abc.abstractmethod
    def _to_response(self, request: ResearchRequest, payload: dict[str, Any]) -> ResearchResponse:
        """Translate a completed run's status payload into a normalized response."""

    def _start(self, request: ResearchRequest) -> ResearchProgress:
        return self._progress(request, self._post(self.research_path, self._build_payload(request)))

    async def _astart(self, request: ResearchRequest) -> ResearchProgress:
        return self._progress(request, await self._apost(self.research_path, self._build_payload(request)))

    def _fetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
        return self._progress(request, self._get(self._status_path(job_id)))

    async def _afetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
        return self._progress(request, await self._aget(self._status_path(job_id)))

    def _status_path(self, job_id: str) -> str:
        """:attr:`status_path` with the job id in it, quoted so an id cannot point the request elsewhere."""
        return self.status_path.format(job_id=quote(job_id, safe=""))

    def _progress(self, request: ResearchRequest, payload: dict[str, Any]) -> ResearchProgress:
        state = self.state_of(str(payload.get("status")), self.research_states)
        job_id = next((payload[field] for field in self.job_id_fields if payload.get(field)), "")
        return ResearchProgress(
            job_id=str(job_id),
            state=state,
            # The status call carries the finished run, so it needs no second fetch.
            response=self._to_response(request, payload) if state == "completed" else None,
            detail=error_detail(payload.get("error")),
        )


class HttpBrowseProvider(_HttpProvider, BrowseProvider):
    """A browse backend over a plain HTTP endpoint.

    As thin as :class:`HttpResearchProvider`, for the same reason: starting,
    polling and stopping a task are three requests whose paths, verbs and shapes
    differ between vendors -- stopping one is a ``PATCH`` on one, a ``PUT`` on
    another, a ``POST`` on a third. What is shared is the client, the
    credential, the redirect pin, HTTP error mapping, a task id that cannot
    rewrite a path, and every request clamped to the caller's remaining wait.
    """

    def __init__(self, *, timeout: float = BROWSE_HTTP_TIMEOUT, **kwargs: Any) -> None:
        """
        Args:
            timeout: Request timeout in seconds. Longer than the search and
                extract default, because on a backend whose task call blocks
                this one request is the whole task.
            **kwargs: As :class:`_HttpProvider`.
        """
        super().__init__(timeout=timeout, **kwargs)
        self._browse_timeout = timeout

    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> httpx.Response:
        """Send one request and raise on an HTTP error, for a call whose success may have no body."""
        response = self._client.request(method, path, json=payload, timeout=self._budget())
        self._raise_for_status(response)
        return response

    async def _acall(self, method: str, path: str, payload: dict[str, Any] | None = None) -> httpx.Response:
        response = await self._async_client.request(method, path, json=payload, timeout=self._budget())
        self._raise_for_status(response)
        return response

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._payload(self._call("POST", path, payload))

    async def _apost(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._payload(await self._acall("POST", path, payload))

    def _get(self, path: str) -> dict[str, Any]:
        return self._payload(self._call("GET", path))

    async def _aget(self, path: str) -> dict[str, Any]:
        return self._payload(await self._acall("GET", path))

    def _task_path(self, template: str, job_id: str) -> str:
        """``template`` with the task id in it, quoted so an id cannot point the request elsewhere."""
        return template.format(job_id=quote(job_id, safe=""))

    def _budget(self) -> float:
        """This request's timeout: the caller's remaining wait, when there is one, never more than the client's."""
        return job_budget(self._browse_timeout)
