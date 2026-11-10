"""What the Nimble research tests share: a stubbed Web Search Agent client behind a real provider.

The payloads the tests script are shaped after the SDK's generated response
models, which are the source of truth for the fields the provider reads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from web_router.providers.nimble import NimbleResearchProvider

AGENT_ID = "agent-1"
RUN_ID = "task_run_9"


def research_run(status: str, **extra: Any) -> dict[str, Any]:
    """A run object, as `agents.run()` and `runs.get()` both return one."""
    return {
        "id": RUN_ID,
        "web_search_agent_id": AGENT_ID,
        "status": status,
        "is_active": status in ("queued", "running"),
        "effort": "medium",
        "interaction_id": "interaction-1",
        "created_at": "2026-01-01T00:00:00Z",
        **extra,
    }


class _Model:
    """Stands in for an SDK response model, which the provider reads through ``model_dump``."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def model_dump(self) -> dict[str, Any]:
        return dict(self._payload)


class _Runs:
    def __init__(self, states: list[dict[str, Any]], result: dict[str, Any] | None) -> None:
        self._states = states
        self._result = result
        self.gets: list[tuple[str, str]] = []
        self.results: list[tuple[str, str]] = []

    def get(self, run_id: str, *, agent_id: str) -> _Model:
        self.gets.append((agent_id, run_id))
        return _Model(self._states.pop(0) if len(self._states) > 1 else self._states[0])

    def result(self, run_id: str, *, agent_id: str) -> _Model:
        self.results.append((agent_id, run_id))
        assert self._result is not None, "the provider asked for an output this run never produced"
        return _Model(self._result)


class _Agents:
    def __init__(self, started: dict[str, Any], states: list[dict[str, Any]], result: dict[str, Any] | None) -> None:
        self._started = started
        self.runs = _Runs(states, result)
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> _Model:
        self.calls.append(kwargs)
        return _Model(self._started)


class _Client:
    def __init__(self, started: dict[str, Any], states: list[dict[str, Any]], result: dict[str, Any] | None) -> None:
        self.agents = _Agents(started, states, result)
        # Every call the provider makes goes through with_options, which is how
        # the caller's remaining research budget reaches the SDK. Recorded
        # rather than ignored so a test can prove the clamp is applied.
        self.timeouts: list[float] = []

    def with_options(self, *, timeout: float, **_: Any) -> _Client:
        self.timeouts.append(timeout)
        return self


class _AsyncRuns:
    def __init__(self, runs: _Runs) -> None:
        self._runs = runs

    async def get(self, run_id: str, *, agent_id: str) -> _Model:
        return self._runs.get(run_id, agent_id=agent_id)

    async def result(self, run_id: str, *, agent_id: str) -> _Model:
        return self._runs.result(run_id, agent_id=agent_id)


class _AsyncAgents:
    def __init__(self, agents: _Agents) -> None:
        self._agents = agents
        self.runs = _AsyncRuns(agents.runs)

    async def run(self, **kwargs: Any) -> _Model:
        return self._agents.run(**kwargs)


class _AsyncClient:
    """The async twin of the stub, recording into the same call log."""

    def __init__(self, client: _Client) -> None:
        self.agents = _AsyncAgents(client.agents)
        self._client = client

    def with_options(self, *, timeout: float, **_: Any) -> _AsyncClient:
        self._client.timeouts.append(timeout)
        return self


def stub_client(
    provider: NimbleResearchProvider,
    *,
    started: dict[str, Any] | None = None,
    states: list[dict[str, Any]] | None = None,
    result: dict[str, Any] | None,
) -> _Client:
    client = _Client(started or research_run("queued"), states or [research_run("completed")], result)
    provider._client = client  # type: ignore[assignment]
    provider._async_client = _AsyncClient(client)  # type: ignore[assignment]
    return client
