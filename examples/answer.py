"""A written answer with its sources, in one call.

`answer()` asks one question and returns the backend's own written answer and
the pages it rests on, in a few seconds -- between `search()`, which returns the
pages, and `research()`, a job of minutes. Nothing here writes or edits the
answer: it is the backend's, as it came.

Needs a key: every answer backend bills the call. With none set this prints
what to set and exits.

Run it with::

    make setup   # in your webrouter clone: every extra
    uv run --env-file .env python examples/answer.py
"""

from __future__ import annotations

from web_router import ProviderNotFoundError, UnsupportedParameterError, WebRouter, configured_answer_providers
from web_router.constants import ANSWER_ENV_BY_PROVIDER
from web_router.registry_answer import get_answer_provider_class

QUESTION = "Who won the 2022 FIFA World Cup final and what was the score?"

CONFIGURED = configured_answer_providers()

if not CONFIGURED:
    variables = ", ".join(sorted({var for envs in ANSWER_ENV_BY_PROVIDER.values() for var in envs}))
    raise SystemExit(f"No answer backend is configured. Set one of {variables} and run this again.")


def ask(router: WebRouter, provider: str) -> None:
    """One question, one backend: the answer, then each source with the marker that cites it."""
    response = router.answer(QUESTION, provider=provider)

    print(f"{response.provider} answered:\n{response.answer}\n")
    for citation in response.citations[:5]:
        # `field` is the marker in the answer ("[2]") when the backend ties the source to a sentence.
        marker = citation.field or "(read, not cited)"
        print(f"  {marker:>18}  {citation.title or citation.url}\n{'':>20}{citation.url}")


def filtered(router: WebRouter, provider: str) -> None:
    """An option the backend does not map raises before anything is sent -- it is never dropped."""
    try:
        response = router.answer(QUESTION, provider=provider, include_domains=["wikipedia.org"], recency="year")
    except UnsupportedParameterError as exc:
        print(f"\n{provider} refused, and nothing was billed: {exc}")
        return
    print(f"\nwith include_domains and recency, {len(response.citations)} sources, all from:")
    print("  " + ", ".join(sorted({c.url.split("/")[2] for c in response.citations})))


def unnamed(router: WebRouter) -> None:
    """With no provider named: the one configured backend answers, or the error says which to pick."""
    try:
        response = router.answer(QUESTION)
    except ProviderNotFoundError as exc:
        print(f"\nno provider named: {exc}")
        return
    print(f"\nno provider named: {response.provider} answered, the one backend configured")


if __name__ == "__main__":
    provider = CONFIGURED[0]
    # A backend that maps both filters, when one is configured; another one shows the refusal instead.
    filtering = next(
        (p for p in CONFIGURED if {"include_domains", "recency"} <= get_answer_provider_class(p).supported_parameters),
        provider,
    )
    with WebRouter() as router:
        ask(router, provider)
        filtered(router, filtering)
        unnamed(router)
