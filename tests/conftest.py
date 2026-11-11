from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path
from typing import Any, ClassVar, get_args

import pytest

from tests._live_data_fake import FakeLiveDataProvider
from web_router.exceptions import ProviderError
from web_router.providers.answer import AnswerProvider
from web_router.providers.base import (
    BrowseProvider,
    BrowserProvider,
    ExtractProvider,
    ResearchProvider,
    SearchProvider,
)
from web_router.registry import (
    register_browse_provider,
    register_browser_provider,
    register_extract_provider,
    register_live_data_provider,
    register_provider,
    register_research_provider,
    unregister_browse_provider,
    unregister_browser_provider,
    unregister_extract_provider,
    unregister_live_data_provider,
    unregister_provider,
    unregister_research_provider,
)
from web_router.registry_answer import register_answer_provider, unregister_answer_provider
from web_router.types import (
    BROWSE_PARAMETERS,
    BROWSER_PARAMETERS,
    EXTRACT_PARAMETERS,
    NORMALIZED_PARAMETERS,
    RESEARCH_PARAMETERS,
    BrowseProgress,
    BrowseRequest,
    BrowseResponse,
    BrowserRequest,
    BrowserSession,
    BrowserState,
    BrowseStep,
    Citation,
    ExtractedDocument,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
    JobState,
    ResearchProgress,
    ResearchRequest,
    ResearchResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
    StealthLevel,
)
from web_router.types_answer import ANSWER_PARAMETERS, AnswerRequest, AnswerResponse

# Load .env before collection, so the integration tests' skipif conditions see
# the keys. Unit tests never read them. Absent in CI, which is the point: no
# key means every integration test skips rather than fails.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
if _ENV_FILE.is_file():
    from dotenv import load_dotenv

    load_dotenv(_ENV_FILE)

pytest_plugins = ["tests.benchmarks._tools_effort_fakes"]
"""The tool and effort runner's fixtures, kept beside its tests rather than in ``tests/benchmarks/conftest.py``."""


class FakeProvider(SearchProvider):
    """An in-memory provider so router tests never touch the network."""

    name = "fake"
    supported_parameters = NORMALIZED_PARAMETERS

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.requests: list[SearchRequest] = []
        self.closed = False

    def _respond(self, request: SearchRequest) -> SearchResponse:
        self.requests.append(request)
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[SearchResult(url="https://example.com", title="Example", content="hello")],
        )

    def _search(self, request: SearchRequest) -> SearchResponse:
        return self._respond(request)

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        return self._respond(request)

    def close(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.closed = True


class FakeExtractProvider(ExtractProvider):
    """An in-memory extract provider so router tests never touch the network."""

    name = "fake"
    supported_parameters = EXTRACT_PARAMETERS
    supported_formats: frozenset[ExtractFormat] = frozenset({"markdown", "html", "text"})
    default_format: ExtractFormat = "markdown"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[list[str]] = []
        self.closed = False

    def _respond(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        self.calls.append(list(urls))
        return ExtractResponse(
            provider=self.name,
            documents=[
                ExtractedDocument(url=url, title="Example", content="hello", format=self.format_of(request))
                for url in urls
            ],
            raw=[{"urls": list(urls)}],
        )

    def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        return self._respond(request, urls)

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        return self._respond(request, urls)

    def close(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fake_provider() -> Any:
    register_provider("fake", FakeProvider)
    yield FakeProvider
    unregister_provider("fake")


@pytest.fixture
def fake_extract_provider() -> Any:
    register_extract_provider("fake", FakeExtractProvider)
    yield FakeExtractProvider
    unregister_extract_provider("fake")


@pytest.fixture
def fake_live_data_provider() -> Any:
    register_live_data_provider("fake", FakeLiveDataProvider)
    yield FakeLiveDataProvider
    unregister_live_data_provider("fake")


@contextmanager
def billing_aware(provider: str) -> Iterator[None]:
    """Turn "this account cannot pay for the call" into a skip, not a failure.

    A 402 says the key is valid and the request well-formed -- the account is
    simply out of credit. That is not something the code can be wrong about, and
    failing on it would train people to ignore a red integration suite. Every
    other status still fails.
    """
    try:
        yield
    except ProviderError as exc:
        response = getattr(exc.original, "response", None)
        if response is not None and response.status_code == HTTPStatus.PAYMENT_REQUIRED:
            pytest.skip(f"{provider} account is out of credits -- cannot verify against the live API")
        raise


class FakeResearchProvider(ResearchProvider):
    """An in-memory research provider so router tests never touch the network.

    Answers after :attr:`polls_before_done` polls, so a test can exercise the
    waiting without waiting: zero completes inside ``submit``, which is also
    how a backend that answers synchronously behaves.
    """

    name = "fake"
    supported_parameters = RESEARCH_PARAMETERS

    polls_before_done = 0
    fails_with: str | None = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.requests: list[ResearchRequest] = []
        self.polls = 0
        self.cancelled: list[str] = []
        self.closed = False

    def _progress(self, request: ResearchRequest) -> ResearchProgress:
        if self.fails_with is not None:
            return ResearchProgress(job_id="job-1", state="failed", detail=self.fails_with)
        if self.polls < self.polls_before_done:
            return ResearchProgress(job_id="job-1", state="running")
        return ResearchProgress(
            job_id="job-1",
            state="completed",
            response=ResearchResponse(
                provider=self.name,
                query=request.query,
                answer="the answer",
                structured={"ok": True} if request.output_schema else None,
                citations=[Citation(url="https://example.com", title="Example")],
            ),
        )

    def _start(self, request: ResearchRequest) -> ResearchProgress:
        self.requests.append(request)
        return self._progress(request)

    async def _astart(self, request: ResearchRequest) -> ResearchProgress:
        return self._start(request)

    def _fetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
        self.polls += 1
        return self._progress(request)

    def _cancel(self, job_id: str) -> bool:
        self.cancelled.append(job_id)
        return True

    def close(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fake_research_provider() -> Any:
    register_research_provider("fake", FakeResearchProvider)
    yield FakeResearchProvider
    unregister_research_provider("fake")


class FakeAnswerProvider(AnswerProvider):
    """An in-memory answer provider so router tests never touch the network."""

    name = "fake"
    supported_parameters = ANSWER_PARAMETERS

    answer_text = "the answer"
    raises: Exception | None = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.requests: list[AnswerRequest] = []
        self.closed = False

    def _answer(self, request: AnswerRequest) -> AnswerResponse:
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        return AnswerResponse(
            provider=self.name,
            query=request.query,
            answer=self.answer_text,
            citations=[Citation(url="https://example.com", title="Example")],
            raw=[{"answer": self.answer_text}],
        )

    async def _aanswer(self, request: AnswerRequest) -> AnswerResponse:
        return self._answer(request)

    def close(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fake_answer_provider() -> Any:
    register_answer_provider("fake", FakeAnswerProvider)
    yield FakeAnswerProvider
    unregister_answer_provider("fake")


FAKE_CDP_URL = "wss://connect.example.test/?apiKey=SEKRIT-cdp-0123456789&sessionId={session_id}"
"""Shaped like a real one: some backends put the account key in the CDP URL."""


class FakeBrowserProvider(BrowserProvider):
    """An in-memory browser provider so router tests never touch the network.

    Keeps a ledger of what it opened and released, which is what the contract
    tests assert on. :attr:`already_over` models a session the backend ended on
    its own: releasing it raises, as a real API's 404 would, and asking about it
    reports ``expired``. ``control_url`` is returned on every open, as the
    vendors that hand one out on open do.
    """

    name = "fake"
    supported_parameters = BROWSER_PARAMETERS
    stealth_levels: ClassVar[frozenset[StealthLevel]] = frozenset(get_args(StealthLevel))

    states: ClassVar[dict[str, BrowserState]] = {"RUNNING": "running", "GONE": "expired", "BROKEN": "failed"}

    def __init__(self, *, control_url: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.control_url_on_open = control_url
        self.requests: list[BrowserRequest] = []
        self.released: list[str] = []
        self.already_over: set[str] = set()
        self.reported = "RUNNING"
        self.closed = False

    def _open(self, request: BrowserRequest) -> BrowserSession:
        self.requests.append(request)
        session_id = f"session-{len(self.requests)}"
        return BrowserSession(
            provider=self.name,
            session_id=session_id,
            cdp_url=FAKE_CDP_URL.format(session_id=session_id),
            control_url=self.control_url_on_open,
            raw={"connectUrl": FAKE_CDP_URL.format(session_id=session_id)},
        )

    async def _aopen(self, request: BrowserRequest) -> BrowserSession:
        return self._open(request)

    def _release(self, session_id: str) -> None:
        if session_id in self.already_over:
            raise RuntimeError(f"HTTP 404: no live session {session_id}")
        self.released.append(session_id)

    async def _arelease(self, session_id: str) -> None:
        self._release(session_id)

    def _fetch(self, session_id: str) -> BrowserState:
        return self.state_of("GONE" if session_id in self.already_over else self.reported, self.states)

    def close(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fake_browser_provider() -> Any:
    register_browser_provider("fake", FakeBrowserProvider)
    yield FakeBrowserProvider
    unregister_browser_provider("fake")


class FakeBrowseProvider(BrowseProvider):
    """An in-memory browse provider so router tests never touch the network.

    Reports its task through a status table of its own, as a real backend does,
    and finishes after :attr:`polls_before_done` polls. :attr:`reported`
    overrides the status it gives, which is how a test makes it say something
    the table does not list.
    """

    name = "fake"
    supported_parameters = BROWSE_PARAMETERS

    states: ClassVar[dict[str, JobState]] = {
        "RUNNING": "running",
        "FINISHED": "completed",
        "FAILED": "failed",
        "STOPPED": "cancelled",
    }

    polls_before_done = 0
    reported: str | None = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.requests: list[BrowseRequest] = []
        self.polls = 0
        self.cancelled: list[str] = []
        self.closed = False

    def _progress(self, request: BrowseRequest) -> BrowseProgress:
        status = self.reported or ("RUNNING" if self.polls < self.polls_before_done else "FINISHED")
        state = self.state_of(status, self.states)
        if state != "completed":
            return BrowseProgress(job_id="task-1", state=state, detail=f"the agent is {status.lower()}")
        return BrowseProgress(
            job_id="task-1",
            state=state,
            response=BrowseResponse(
                provider=self.name,
                task=request.task,
                answer="the answer",
                structured={"ok": True} if request.output_schema else None,
                steps=[BrowseStep(action="navigate", url=request.start_url, extra={"stepNumber": 1})],
                request_id="task-1",
                raw=[{"status": status, "sessionUrl": "wss://connect.example.test/?token=SEKRIT"}],
            ),
        )

    def _start(self, request: BrowseRequest) -> BrowseProgress:
        self.requests.append(request)
        return self._progress(request)

    async def _astart(self, request: BrowseRequest) -> BrowseProgress:
        return self._start(request)

    def _fetch(self, request: BrowseRequest, job_id: str) -> BrowseProgress:
        self.polls += 1
        return self._progress(request)

    def _cancel(self, job_id: str) -> bool:
        self.cancelled.append(job_id)
        return True

    def close(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fake_browse_provider() -> Any:
    register_browse_provider("fake", FakeBrowseProvider)
    yield FakeBrowseProvider
    unregister_browse_provider("fake")


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A fake clock for the deadline tests; imported here so a bare conftest load stays light."""
    from tests.deadline_helpers import install_clock

    return install_clock(monkeypatch)


from tests.image_helpers import jpeg as jpeg  # noqa: E402  imported from here by the screenshot tests
from tests.routing_helpers import called, priced, research_backends, tool  # noqa: E402, F401  the router fixtures
