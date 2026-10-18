"""Agentic research: prose, structure and enrichment, and how to wait for one.

`research()` is the third method and the one that behaves differently. A search
answers in a second; a research run reasons over several turns and answers in
minutes, so it is a job rather than a call -- and this script shows both ways to
hold one: blocking on it, and submitting it and going away.

Needs a key. Every research backend bills for the run, so unlike `search` and
`extract` there is no anonymous tier to fall back on: with no credential at all
this prints what to set and exits.

Run it with::

    make setup   # in your webrouter clone: every extra
    uv run --env-file .env python examples/research.py
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from web_router import ResearchTimeoutError, WebRouter, configured_research_providers

QUESTION = "what does nimbleway sell, and who are its closest competitors"

USABLE = [name for name in configured_research_providers() if name != "auto"]

if not USABLE:
    raise SystemExit(
        "No research backend is configured. Set one of NIMBLE_API_KEY, PARALLEL_API_KEY, "
        "PERPLEXITY_API_KEY, LINKUP_API_KEY or YOU_API_KEY and run this again."
    )


class Company(BaseModel):
    """What we want back, as a schema rather than as prose."""

    name: str
    what_they_sell: str
    headquarters: str | None = Field(default=None)


def prose(router: WebRouter) -> None:
    """The simplest form: a question in, a cited answer out."""
    print(f"researching: {QUESTION}\n(this takes minutes, not seconds)")

    response = router.research(QUESTION, effort="low")

    print(f"\n{response.provider} answered, {response.outcome}")
    print(response.answer)
    for citation in response.citations[:5]:
        print(f"  - {citation.title or citation.url} ({citation.url})")


def structured(router: WebRouter) -> None:
    """The same run, asked for an object instead of paragraphs.

    Pass the pydantic model straight in; the router takes the JSON Schema off
    it. A backend that cannot return structured output says so rather than
    handing back prose under the name of a schema.
    """
    response = router.research(
        "what does nimbleway sell and where is it based",
        output_schema=Company,
        effort="low",
    )
    print(f"\nstructured, via {response.provider}:\n{response.structured}")


def enrichment(router: WebRouter) -> None:
    """Rows you already hold, filled in from the web -- one object per record."""
    response = router.research(
        "fill in what each company sells and where it is headquartered",
        output_schema=Company,
        records=[{"name": "Nimble"}, {"name": "Tavily"}],
        effort="low",
    )
    print(f"\nenriched, via {response.provider}:\n{response.structured}")


def without_waiting(router: WebRouter) -> None:
    """Submit the run, do something else, come back for it.

    This is the shape to use anywhere blocking for minutes is not acceptable --
    a request handler, a UI, an MCP server. Nothing is lost by giving up on the
    wait: the run keeps going and the job is still yours to poll.
    """
    job = router.submit_research(QUESTION, effort="low")
    print(f"\nsubmitted {job.job_id} to {job.provider}; state is {job.state}")

    try:
        response = job.result(timeout=30)
    except ResearchTimeoutError as exc:
        print(f"still running after {exc.timeout:g}s -- polling again would pick it back up")
        return

    print(f"finished after all: {len(response.citations)} citations")


def main() -> None:
    with WebRouter() as router:
        prose(router)
        structured(router)
        enrichment(router)
        without_waiting(router)


if __name__ == "__main__":
    main()
