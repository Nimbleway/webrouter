"""What every ``auto`` chain is made of, shared by the modules that build one.

A chain is the backends one method's ``auto`` tries, in order, each a name
beside the kwargs to build it with. Resolving one -- the default for the
current environment, or the caller's ``chain`` as a list or keyed by method --
and deciding what a member's failure means for the next are the same for
every method, so they live here once. The chains themselves are in
:mod:`web_router.providers.auto` and :mod:`web_router.providers.auto_template`.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Mapping, Sequence
from http import HTTPStatus
from typing import Any, TypeAlias, TypeVar

from web_router.constants import (
    AUTO_AGGREGATE_FAILURE,
    AUTO_CHAIN_METHODS,
    AUTO_CLIENT_OPTION,
    AUTO_CLIENT_PROVIDERS,
    AUTO_EXTRA,
    AUTO_RESEARCH_PREFERRED,
    AUTO_SETTING_PROVIDERS,
    MSG_AUTO_CHAIN_ONE_NAME,
    MSG_AUTO_CHAIN_UNKNOWN_METHOD,
    MSG_AUTO_CLIENT_AND_KEY,
    MSG_AUTO_RESEARCH_EXHAUSTED,
    MSG_AUTO_RESEARCH_NO_CREDENTIAL,
    MSG_OPTION_VALUE,
    RESEARCH_ENV_BY_PROVIDER,
)
from web_router.exceptions import (
    ConfigurationError,
    DeadlineExceededError,
    ProviderError,
    ProviderNotInstalledError,
    WebRouterError,
)
from web_router.providers._deadline import current_deadline
from web_router.routing.attempts import noted
from web_router.routing.contract import ToolName
from web_router.types import ExtractResponse, SearchResponse
from web_router.types import provider_name as _lookup_name
from web_router.types_answer import AnswerResponse

_Entry: TypeAlias = "str | tuple[str, Mapping[str, Any]]"
"""One member as a caller writes it: a backend's name, or its name beside the kwargs to build it with."""

_ChainSpec: TypeAlias = "Sequence[_Entry] | Mapping[str, Sequence[_Entry]]"
"""A ``chain`` argument: the members of every method's chain, or each method's own, keyed by method."""

_RECOVERABLE = (ProviderNotInstalledError, ConfigurationError, ProviderError)
"""Failures that mean "try the next backend".

A missing extra, a missing credential and a backend that refused the call are
all things another member of the chain may not suffer from. Anything else --
notably :class:`UnsupportedParameterError` -- is a bug in this class rather
than a property of the backend, and propagates.
"""


def _keyed_chain(preferred: tuple[str, ...], configured: set[str]) -> tuple[tuple[str, dict[str, Any]], ...]:
    """The preferred backends you actually hold credentials for, in order."""
    return tuple((name, {}) for name in preferred if name in configured)


def _normalized(members_config: Mapping[str, Mapping[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """``members_config`` keyed as chain members are -- an enum or odd casing matched to the member it names."""
    return {_lookup_name(name): dict(config) for name, config in (members_config or {}).items()}


def _default_chain(
    default_chain: Callable[..., tuple[tuple[str, dict[str, Any]], ...]], in_code: frozenset[str]
) -> tuple[tuple[str, dict[str, Any]], ...]:
    """A class's default chain, with the backends keyed in code -- unless its override takes no argument.

    A subclass, or a test, may replace ``default_chain`` with one that takes
    nothing; that chain is its own choice, and the keys in code cannot widen it.
    """
    if in_code and inspect.signature(default_chain).parameters:
        return default_chain(in_code)
    return default_chain()


def _members_and_chain(
    chain: _ChainSpec | None,
    members_config: Mapping[str, Mapping[str, Any]] | None,
    method: str,
    default_chain: Callable[..., tuple[tuple[str, dict[str, Any]], ...]],
) -> tuple[dict[str, dict[str, Any]], tuple[tuple[str, dict[str, Any]], ...]]:
    """What every ``auto`` resolves at construction: its members' config, and the chain one method tries.

    A backend keyed in ``members_config`` joins the default chain as one keyed in the environment does.
    """
    config = _normalized(members_config)
    in_code = _configured_in(config)
    resolved = _chain_for(chain, method, lambda: _default_chain(default_chain, in_code))
    _refuse_a_key_beside_a_client(config, resolved)
    return config, resolved


def _refuse_a_key_beside_a_client(
    members_config: Mapping[str, Mapping[str, Any]], chain: tuple[tuple[str, dict[str, Any]], ...]
) -> None:
    """Refuse, at construction, a client-taking member that would be built with an ``api_key`` beside its client.

    Only the backends in ``AUTO_CLIENT_PROVIDERS``, whose key would replace the
    client's credential; any other provider is left to make what it will of both.

    The member refuses that itself, but only when it is built, mid-call, where
    the error is one this chain recovers from -- so the call would quietly go
    to the next backend rather than say why the client went unused.

    Raises:
        ConfigurationError: naming the backend.
    """
    for name, options in chain:
        if name not in AUTO_CLIENT_PROVIDERS:
            continue
        merged = _member_options(members_config, name, options)
        if merged.get(AUTO_CLIENT_OPTION) is not None and merged.get("api_key") is not None:
            raise ConfigurationError(MSG_AUTO_CLIENT_AND_KEY.format(name=name))


def _member_options(
    members_config: Mapping[str, Mapping[str, Any]], name: str, options: Mapping[str, Any]
) -> dict[str, Any]:
    """A member's constructor kwargs: its own config, the chain's ``(name, kwargs)`` laid over it."""
    return {**members_config.get(name, {}), **options}


def _configured_in(members_config: Mapping[str, Mapping[str, Any]]) -> frozenset[str]:
    """The backends handed a key, a client or their setting in code, as ``provider_config``."""
    return frozenset(
        name
        for name, config in members_config.items()
        if config.get("api_key")
        or (name in AUTO_CLIENT_PROVIDERS and config.get(AUTO_CLIENT_OPTION))
        or (name in AUTO_SETTING_PROVIDERS and config.get(AUTO_SETTING_PROVIDERS[name]))
    )


def _keyless_after(
    keyed: tuple[tuple[str, dict[str, Any]], ...], tier: tuple[tuple[str, dict[str, Any]], ...]
) -> tuple[tuple[str, dict[str, Any]], ...]:
    """The keyless tier, less any backend the chain tries with a key, before or after it.

    A keyless member resolves a key when one is set -- Tavily's, Firecrawl's and
    Keenable's ``allow_keyless`` all do -- so keeping it beside its keyed self
    would only send the same request, with the same credential, a second time.
    """
    tried = {name for name, _ in keyed}
    return tuple(member for member in tier if member[0] not in tried)


def _chain_for(
    chain: _ChainSpec | None,
    method: str,
    default: Callable[[], tuple[tuple[str, dict[str, Any]], ...]],
) -> tuple[tuple[str, dict[str, Any]], ...]:
    """The chain one method's ``auto`` tries: what ``chain`` gives that method, else its default.

    Keyed by method as well as listed because the router hands
    ``provider_config["auto"]`` to the ``auto`` of every method, so a list
    there is every method's chain -- and a search order naming Brave would
    leave extract with a backend it does not have. Keyed, it sets the methods
    it names and leaves the others on their defaults.

    Raises:
        ConfigurationError: for a key that names no method, or a bare name
            where a list belongs, which would otherwise be read letter by letter.
    """
    listed: Sequence[_Entry] | None
    if isinstance(chain, Mapping):
        unknown = set(chain) - AUTO_CHAIN_METHODS
        if unknown:
            raise ConfigurationError(
                MSG_AUTO_CHAIN_UNKNOWN_METHOD.format(names=_quoted(unknown), methods=_quoted(AUTO_CHAIN_METHODS))
            )
        listed = chain.get(method)
    else:
        listed = chain
    if listed is None:
        return default()
    if isinstance(listed, str):
        raise ConfigurationError(MSG_AUTO_CHAIN_ONE_NAME.format(name=listed))
    return tuple(_entry(item) for item in listed)


def _entry(item: _Entry) -> tuple[str, dict[str, Any]]:
    if isinstance(item, str):
        return _lookup_name(item), {}
    name, options = item
    return _lookup_name(name), dict(options)


def _nothing_started(exc: WebRouterError) -> bool:
    """Whether a member's failure proves it left nothing billing, so the next member may be tried.

    Shared by every chain whose members start something that bills until it
    ends -- a browser session, a browse task. The allowlist is exactly:

    - :class:`ConfigurationError` and :class:`ProviderNotInstalledError`, which
      fail before any request is sent;
    - a :class:`ProviderError` whose ``original`` is an ``httpx.HTTPStatusError``
      with a 4xx status, an answer that refused;
    - a :class:`ProviderError` whose ``original`` is an ``httpx.ConnectError``
      or ``httpx.ConnectTimeout``, a request that never reached the backend;
    - the same two, as the ``nimble-python`` SDK reports them: an
      ``APIStatusError`` with a 4xx status, or an ``APIConnectionError`` that
      is not its ``APITimeoutError``.

    Everything else raises: a read that timed out, a 5xx from a gateway in
    front of the backend, a 200 that could not be read. The backend may have
    acted on any of those, and starting a second one would bill twice.
    """
    if isinstance(exc, (ConfigurationError, ProviderNotInstalledError)):
        return True
    if not isinstance(exc, ProviderError):
        return False
    # Imported here: httpx arrives with an extra, and without it no request
    # can have been sent to be refused.
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx ships with every extra
        return False
    original = exc.original
    if isinstance(original, httpx.HTTPStatusError):
        return bool(original.response.is_client_error)
    if isinstance(original, (httpx.ConnectError, httpx.ConnectTimeout)):
        return True
    return _sdk_refused(original)


def _sdk_refused(original: BaseException | None) -> bool:
    """The same two shapes as the ``nimble-python`` SDK reports them, which are not httpx errors.

    Its ``APIStatusError`` carries the status; a 4xx is an answer that
    refused. Its ``APIConnectionError`` is a request that never reached the
    backend -- except its ``APITimeoutError`` subclass, which covers a read
    that timed out after the request was accepted, and so proves nothing.
    """
    try:
        from nimble_python import APIConnectionError, APIStatusError, APITimeoutError
    except ImportError:
        return False
    if isinstance(original, APIStatusError):
        # bool(): the SDK is typed, but a mypy run without it installed sees Any here.
        return bool(HTTPStatus.BAD_REQUEST <= original.status_code < HTTPStatus.INTERNAL_SERVER_ERROR)
    return isinstance(original, APIConnectionError) and not isinstance(original, APITimeoutError)


def _exhausted(
    provider: str,
    failures: dict[str, WebRouterError],
    template: str = AUTO_AGGREGATE_FAILURE,
) -> WebRouterError:
    """Report every backend's own reason, so the cause is not hidden behind "auto"."""
    # Nothing installed is a setup problem with one fix, not a row of backend
    # failures to read through. Say so in the form that names it.
    if failures and all(isinstance(exc, ProviderNotInstalledError) for exc in failures.values()):
        return ProviderNotInstalledError(provider, AUTO_EXTRA)

    detail = "; ".join(f"{name}: {exc}" for name, exc in failures.items())
    return ProviderError(provider, template.format(failures=detail))


def _empty_search(response: SearchResponse) -> bool:
    """No results and no answer: nothing a caller could use."""
    return not response.results and not (response.answer or "").strip()


def _empty_extract(response: ExtractResponse) -> bool:
    """Every document blank. One with text makes the answer usable, as a partial fetch is."""
    return not any(document.content.strip() for document in response.documents)


def _empty_answer(response: AnswerResponse) -> bool:
    """A blank written answer: nothing a caller could use."""
    return not (response.answer or "").strip()


_Answer = TypeVar("_Answer", SearchResponse, ExtractResponse, AnswerResponse)


def _held_or_raise(
    held: _Answer | None,
    provider: str,
    failures: dict[str, WebRouterError],
    template: str = AUTO_AGGREGATE_FAILURE,
    refused: Sequence[WebRouterError] = (),
) -> _Answer:
    """The first empty answer when one came back, else every member's reason.

    An empty result is an answer: it outranks the failures that followed it,
    from members that were never tried before an empty answer fell through.
    When every member tried refused the request before sending and none
    failed, the first refusal is raised as it came: nothing was asked.
    """
    if held is not None:
        return held
    if refused and not failures:
        raise refused[0]
    raise _exhausted(provider, failures, template)


def _out_of_time() -> bool:
    """Whether a deadline is in force and has passed, so no further member may start.

    Checked before each member of a one-call chain -- search, extract, answer.
    No time is held back for later members: each one runs under its own
    timeout clamped to whatever is left, so a member that hangs gives up at
    its own timeout, the next starts with what remains, and the last runs to
    the deadline itself. The billed chains never check it: a research or
    browse submit is clamped as on its own, never cut short for a member after it.
    """
    deadline = current_deadline()
    return deadline is not None and deadline.left() <= 0


def _late_answer(held: _Answer | None, provider: str, failures: dict[str, WebRouterError]) -> _Answer | None:
    """``None`` while time is left for the next member; once it has run out, the first empty answer, else the deadline.

    Checked at the head of each member of a one-call chain.
    """
    if not _out_of_time():
        return None
    return _held_or_late(held, provider, failures)


def _held_or_late(
    held: _Answer | None,
    provider: str,
    failures: dict[str, WebRouterError],
    late: DeadlineExceededError | None = None,
    member: str | None = None,
    tool: ToolName | None = None,
) -> _Answer:
    """The first empty answer when one came back before the deadline, else the deadline, naming what was tried.

    ``late`` is the deadline error ``member`` raised, when it was one that ran
    into it rather than the chain finding no time left before the next; it is
    reported to the call's failure hook as ``tool``'s.
    """
    if late is not None and member is not None and tool is not None:
        noted(tool, member, late)
    if held is not None:
        return held
    raise _deadline_error(provider, failures, late, member) from late


def _deadline_error(
    provider: str,
    failures: Mapping[str, WebRouterError],
    late: DeadlineExceededError | None = None,
    member: str | None = None,
) -> DeadlineExceededError:
    """The deadline of the call in flight, as a chain's error naming each member's reason."""
    tried: dict[str, WebRouterError] = dict(failures)
    if late is not None and member is not None:
        tried[member] = late
    deadline = current_deadline()
    seconds = late.seconds if late is not None else deadline.seconds if deadline is not None else 0.0
    detail = "; ".join(f"{name}: {exc}" for name, exc in tried.items()) or None
    return DeadlineExceededError(provider, seconds, tried=detail)


def _no_backend(
    provider: str,
    failures: dict[str, WebRouterError],
    *,
    preferred: tuple[str, ...] = AUTO_RESEARCH_PREFERRED,
    env_by_provider: dict[str, tuple[str, ...]] = RESEARCH_ENV_BY_PROVIDER,
    no_credential: str = MSG_AUTO_RESEARCH_NO_CREDENTIAL,
    exhausted: str = MSG_AUTO_RESEARCH_EXHAUSTED,
) -> WebRouterError:
    """Say why no run started: what each backend reported, or that none was configured.

    An empty chain is not a row of failures -- it is the one setup problem a
    method with no keyless tier has, and every variable that would fix it is
    known. Defaults to research's tables; browse passes its own.
    """
    if failures:
        return _exhausted(provider, failures, exhausted)

    env_vars = [env_var for name in preferred for env_var in env_by_provider.get(name, ())]
    return ConfigurationError(no_credential.format(env_vars=", ".join(env_vars)))


def _quoted(names: Iterable[str]) -> str:
    return ", ".join(repr(name) for name in sorted(names))


def _valued(values: Mapping[str, Any]) -> str:
    return ", ".join(MSG_OPTION_VALUE.format(name=name, value=values[name]) for name in sorted(values))
