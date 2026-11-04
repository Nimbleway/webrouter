"""Every exception web-router raises.

Provider SDKs raise their own error types. The router wraps them so callers can
handle failures uniformly regardless of which backend served the request:

    WebRouterError
    ├── ConfigurationError          bad or missing configuration
    ├── ProviderNotFoundError       no provider registered under that name
    │   └── NoAnswerProviderError       answer() named no backend and none could be chosen
    ├── ProviderNotInstalledError   provider known, optional dependency missing
    ├── UnsupportedParameterError   provider does not map a requested parameter
    ├── UnsupportedFormatError      provider cannot return a requested format
    ├── MissingParameterError       provider needs a parameter the request left unset
    ├── ResearchTimeoutError        a research run outlasted the time allowed for it
    ├── BrowseTimeoutError          a browse task outlasted the time allowed for it
    ├── BenchmarkTimeoutError       one benchmark row outlasted the comparison's timeout
    ├── DeadlineExceededError       a call outlasted its per-call deadline
    ├── ProviderError               the backend itself failed
    └── BrowserActionError          a page action on an open browser session failed
        ├── StaleElementError           a snapshot ref no longer names an element
        ├── ElementNotFoundError        a selector matched nothing in time
        ├── NavigationError             a page did not load
        ├── BrowserActionTimeoutError   an action outlasted its timeout
        └── PageClosedError             the page, its connection or its session is gone

Message text lives in :mod:`web_router.constants`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from web_router.constants import (
    MSG_BENCHMARK_TIMEOUT,
    MSG_BROWSE_TIMEOUT,
    MSG_BROWSER_ACTION_FAILED,
    MSG_DEADLINE_EXCEEDED,
    MSG_DEADLINE_TRIED,
    MSG_MISSING_PARAMETERS,
    MSG_MISSING_PARAMETERS_ADVICE,
    MSG_MISSING_SETTING,
    MSG_NO_API_KEY,
    MSG_OPTION_VALUE,
    MSG_PROVIDER_NOT_INSTALLED,
    MSG_RESEARCH_TIMEOUT,
    MSG_TEMPLATE_NOT_FOUND,
    MSG_UNKNOWN_PROVIDER,
    MSG_UNSUPPORTED_FORMAT,
    MSG_UNSUPPORTED_FORMAT_ADVICE,
    MSG_UNSUPPORTED_PARAMETERS,
    MSG_UNSUPPORTED_PARAMETERS_ADVICE,
    MSG_UNSUPPORTED_VALUES,
)
from web_router.constants_answer import MSG_ANSWER_NO_DEFAULT, MSG_ANSWER_NONE_CONFIGURED


def describe(exc: BaseException) -> str:
    """What to say about ``exc`` in a message: its text, or its type when it has none.

    httpx's timeouts carry no text, so a read timeout wrapped as
    ``"answer failed: {error}"`` read "answer failed: " and said nothing.
    """
    return str(exc) or type(exc).__name__


class WebRouterError(Exception):
    """Base class for every error raised by web-router."""


class ConfigurationError(WebRouterError):
    """The router or a provider was configured incorrectly."""

    @classmethod
    def missing_api_key(cls, provider: str, env_var: str) -> ConfigurationError:
        """Build the error for a provider with no usable credential."""
        return cls(MSG_NO_API_KEY.format(provider=provider, env_var=env_var))

    @classmethod
    def missing_setting(cls, provider: str, *, env_var: str, setting: str) -> ConfigurationError:
        """Build the error for a required setting that is not a credential.

        Oxylabs authenticates with a username beside its key, and Bright Data
        routes through a zone you create in its console. Neither has a default
        worth guessing, so both are named the same way a missing key is.
        """
        return cls(MSG_MISSING_SETTING.format(provider=provider, env_var=env_var, setting=setting))


class ProviderNotFoundError(WebRouterError):
    """A provider was requested by a name that is not registered."""

    def __init__(self, name: str, available: Iterable[str]) -> None:
        self.name = name
        # Deduplicated: the router looks a default up in both method registries,
        # and a provider serving both would otherwise be listed twice.
        self.available = sorted(set(available))
        super().__init__(MSG_UNKNOWN_PROVIDER.format(name=name, available=", ".join(self.available)))


class NoAnswerProviderError(ProviderNotFoundError):
    """``answer()`` was called with no provider, and none could be chosen, or ``auto`` had no backend to try.

    A :class:`ProviderNotFoundError`, so an ``except`` for that still catches it.
    :attr:`configured` names the answer backends with a key -- several, so which
    to call is the caller's choice -- and when there are none, :attr:`env_vars`
    names the variables that would add one.
    """

    def __init__(self, default: str, available: Iterable[str], configured: Iterable[str], env_vars: Iterable[str]):
        self.name = default
        self.available = sorted(set(available))
        self.configured = sorted(configured)
        self.env_vars = sorted(env_vars)
        if self.configured:
            message = MSG_ANSWER_NO_DEFAULT.format(
                default=default, count=len(self.configured), available=", ".join(self.configured)
            )
        else:
            message = MSG_ANSWER_NONE_CONFIGURED.format(default=default, env_vars=", ".join(self.env_vars) or "none")
        WebRouterError.__init__(self, message)


class ProviderNotInstalledError(WebRouterError):
    """A provider is registered but its optional dependency is not installed."""

    def __init__(self, name: str, extra: str) -> None:
        self.name = name
        self.extra = extra
        super().__init__(MSG_PROVIDER_NOT_INSTALLED.format(name=name, extra=extra))


class UnsupportedParameterError(WebRouterError):
    """A request set a parameter the chosen provider does not map.

    Raised rather than dropping the parameter, so a query never runs under
    weaker constraints than the caller asked for. The message points at
    ``provider_options`` and at adding the mapping, because for a deliberately
    minimal provider this usually means "not mapped yet" rather than
    "impossible".
    """

    def __init__(
        self,
        provider: str,
        parameters: Iterable[str],
        *,
        detail: str | None = None,
        values: Mapping[str, Any] | None = None,
    ) -> None:
        """
        Args:
            detail: Replaces the advice that follows the fact, for a router of
                backends -- ``auto`` -- whose useful answer is why each one was
                passed over rather than where to add a mapping.
            values: The value each of ``parameters`` was set to, for a backend
                that maps the option but not that value -- a stealth level it
                lacks, a country it has no proxy in. The message then names
                ``stealth='off'`` rather than the option alone, and
                :attr:`values` keeps them.
        """
        self.provider = provider
        self.parameters = sorted(parameters)
        self.values = dict(values or {})
        names = ", ".join(_named(name, self.values) for name in self.parameters)
        template = MSG_UNSUPPORTED_VALUES if self.values else MSG_UNSUPPORTED_PARAMETERS
        fact = template.format(provider=provider, names=names)
        super().__init__(f"{fact} {detail or MSG_UNSUPPORTED_PARAMETERS_ADVICE}")


class UnsupportedFormatError(WebRouterError):
    """A request asked for an output format the chosen provider cannot return.

    Separate from :class:`UnsupportedParameterError`: the provider does map
    ``format``, it just cannot produce this one. Raised rather than returning a
    different representation under the requested name, because a caller feeding
    the result to a markdown parser would otherwise get HTML and no warning.
    """

    def __init__(self, provider: str, requested: str, supported: Iterable[str], *, detail: str | None = None) -> None:
        """
        Args:
            detail: Replaces the advice that follows the fact, as on
                :class:`UnsupportedParameterError`.
        """
        self.provider = provider
        self.requested = requested
        self.supported = sorted(supported)
        fact = MSG_UNSUPPORTED_FORMAT.format(
            provider=provider,
            requested=requested,
            supported=", ".join(repr(name) for name in self.supported) or "nothing",
        )
        super().__init__(f"{fact} {detail or MSG_UNSUPPORTED_FORMAT_ADVICE.format(requested=requested)}")


class MissingParameterError(WebRouterError):
    """A request left unset a parameter the chosen provider cannot start without.

    The mirror of :class:`UnsupportedParameterError`, and raised at the same
    point: before any run starts, so nothing is billed for a call that could
    never have worked. A browse backend with no way to open a page on its own
    needs ``start_url``, and folding "start at X" into the task text instead
    would be a rewrite of the caller's words only one backend knows about.
    """

    def __init__(self, provider: str, parameters: Iterable[str], *, detail: str | None = None) -> None:
        """
        Args:
            detail: Replaces the advice that follows the fact, as on
                :class:`UnsupportedParameterError`.
        """
        self.provider = provider
        self.parameters = sorted(parameters)
        names = ", ".join(repr(name) for name in self.parameters)
        fact = MSG_MISSING_PARAMETERS.format(provider=provider, names=names)
        super().__init__(f"{fact} {detail or MSG_MISSING_PARAMETERS_ADVICE}")


class ResearchTimeoutError(WebRouterError):
    """A research run did not finish within the time the caller allowed.

    Not a failure of the run, which is still going: raised instead of returning
    an empty answer, and carrying :attr:`job_id` so a caller who wants to keep
    waiting can pick the same run back up rather than paying to start it again.
    """

    def __init__(self, provider: str, job_id: str, timeout: float) -> None:
        self.provider = provider
        self.job_id = job_id
        self.timeout = timeout
        super().__init__(MSG_RESEARCH_TIMEOUT.format(provider=provider, job_id=job_id, timeout=timeout))


class BrowseTimeoutError(WebRouterError):
    """A browse task did not finish within the time the caller allowed.

    The browse counterpart of :class:`ResearchTimeoutError`: the task is still
    going, and :attr:`job_id` picks it back up rather than paying to start it
    again.
    """

    def __init__(self, provider: str, job_id: str, timeout: float) -> None:
        self.provider = provider
        self.job_id = job_id
        self.timeout = timeout
        super().__init__(MSG_BROWSE_TIMEOUT.format(provider=provider, job_id=job_id, timeout=timeout))


class BenchmarkTimeoutError(WebRouterError):
    """One provider had not answered the comparison's call within its timeout -- a search, or an extract.

    Never raised: it is the :attr:`~web_router.BenchmarkRun.error` of that
    provider's row, so a slow backend costs the comparison one row rather than
    the whole report. It is the budget speaking, not the backend failing --
    which is why it is not a :class:`ProviderError`.
    """

    def __init__(self, provider: str, timeout: float) -> None:
        self.provider = provider
        self.timeout = timeout
        super().__init__(MSG_BENCHMARK_TIMEOUT.format(provider=provider, timeout=timeout))


class DeadlineExceededError(WebRouterError):
    """A call had not answered by the deadline set for it.

    Experimental, as every public name is before 1.0. Raised by a search, an
    extract that brought nothing back in time, a template run or an answer
    whose per-call deadline ran out -- see
    :func:`web_router.providers._deadline.call_deadline`. Research and browse keep
    their own timeout errors, which carry the job id to resume.

    Not a :class:`ProviderError`, for the reason :class:`BenchmarkTimeoutError`
    is not: it is the budget speaking, not the backend failing, so ``auto``
    does not take it as a reason to try the next member -- there is no time
    left to try one in. The backend's own timeout, when there was one, is on
    ``__cause__``.
    """

    def __init__(self, provider: str, seconds: float, *, tried: str | None = None) -> None:
        self.provider = provider
        self.seconds = seconds
        message = MSG_DEADLINE_EXCEEDED.format(provider=provider, seconds=seconds)
        super().__init__(message + (MSG_DEADLINE_TRIED.format(failures=tried) if tried else ""))


class ProviderError(WebRouterError):
    """A provider failed while serving a request.

    The original SDK or HTTP exception is available as :attr:`original` and as
    ``__cause__``.
    """

    def __init__(self, provider: str, message: str, *, original: BaseException | None = None) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.original = original


class TemplateNotFoundError(ProviderError):
    """A template run or describe named a template the backend does not have.

    A :class:`ProviderError`, because the backend answered and said so, with
    its own class so a caller can tell a wrong name from a run that failed.
    Template names are the vendor's own: the same page has a different name
    on each.
    """

    def __init__(self, provider: str, template: str, *, original: BaseException | None = None) -> None:
        self.template = template
        super().__init__(provider, MSG_TEMPLATE_NOT_FOUND.format(name=template), original=original)


class BrowserActionError(WebRouterError):
    """A page action -- ``goto``, ``click`` and the rest -- failed on an open browser session.

    Carries the :attr:`provider` and :attr:`session_id` it ran on and the
    :attr:`action` that failed; the Playwright error, when there was one, is
    ``__cause__``. The subclasses name the failures an agent can act on
    differently: a stale ref wants a new snapshot, a timeout a retry, a closed
    page a new session.
    """

    def __init__(self, provider: str, session_id: str, action: str, detail: str) -> None:
        self.provider = provider
        self.session_id = session_id
        self.action = action
        self.detail = detail
        super().__init__(
            MSG_BROWSER_ACTION_FAILED.format(provider=provider, action=action, session_id=session_id, detail=detail)
        )


class StaleElementError(BrowserActionError):
    """A ref from a snapshot no longer names an element.

    Raised when the ref is not in the handle's latest snapshot -- a newer one
    replaced it, or the page navigated -- or when the element it named was
    removed or re-rendered. Never answered by acting on whatever now sits where
    the element was: take a new snapshot and use a ref from it.
    """


class ElementNotFoundError(BrowserActionError):
    """A CSS or Playwright selector matched no element before the action timed out."""


class NavigationError(BrowserActionError):
    """``goto`` could not load the page: a DNS failure, a refused connection, a missing file."""


class BrowserActionTimeoutError(BrowserActionError):
    """An action did not finish within its timeout, for a reason other than a missing element."""


class PageClosedError(BrowserActionError):
    """The page, the connection to it or the session behind it is gone. Open a new session."""


def _named(parameter: str, values: Mapping[str, Any]) -> str:
    return MSG_OPTION_VALUE.format(name=parameter, value=values[parameter]) if parameter in values else repr(parameter)
