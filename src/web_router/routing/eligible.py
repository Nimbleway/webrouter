"""Which members of a method's ``auto`` can serve a request, for :mod:`web_router.routing.candidates`.

Experimental. :func:`eligible_members` keeps the members ``auto``'s own
``_eligible`` keeps -- those that map every option the request sets -- among
the ones ``auto`` can build, and names each one it cannot build, with why.
"""

from __future__ import annotations

from typing import Any

from web_router.constants import PROVIDER_AUTO
from web_router.constants_candidates import (
    AUTO_GETTERS,
    KEYLESS_OPTION,
    MSG_DROPPED_NOT_CONFIGURED,
    MSG_DROPPED_NOT_INSTALLED,
)
from web_router.exceptions import (
    ConfigurationError,
    MissingParameterError,
    NoAnswerProviderError,
    ProviderNotInstalledError,
    UnsupportedFormatError,
    UnsupportedParameterError,
)
from web_router.routing.contract import ToolName
from web_router.types import ExtractRequest, NormalizedRequest

_NO_MEMBER_LEFT = (UnsupportedParameterError, UnsupportedFormatError, MissingParameterError, NoAnswerProviderError)
"""What an ``auto``'s ``_eligible`` raises when no member can serve the request: no candidates, not a failure."""


def eligible_members(
    router: Any, tool: ToolName, request: NormalizedRequest
) -> tuple[list[tuple[str, bool]], dict[str, str]]:
    """The members of ``tool``'s ``auto`` that can serve ``request``, and the ones it cannot build.

    The first list is in chain order, each member with whether it is keyless;
    the mapping names each member that cannot be built, with why. ``auto``
    passes over a member that does not map an option the request sets, and
    raises when none is left; here that is a method with no candidates. Any
    other error is the caller's setup, and raises as it would from ``auto``.
    """
    auto = getattr(router, AUTO_GETTERS[tool])(PROVIDER_AUTO)
    built, unbuilt = _built(auto)
    # A member that cannot be built may not be readable either, which research's and
    # browse's eligibility would raise on: they read an auto of the built members alone.
    view = auto
    if unbuilt:
        view = type(auto)(chain={tool: [auto._chain[at] for at in built]}, members_config=auto._members_config)
    try:
        if isinstance(request, ExtractRequest):
            members = view._eligible(view.format_of(request))
        else:
            members = view._eligible(request)
    except _NO_MEMBER_LEFT:
        return [], unbuilt
    chain = auto._chain  # the keyless tier is hand-ordered, whatever route-eval ranked the keyed member
    return [
        (name, bool(chain[built[at]][1].get(KEYLESS_OPTION)))
        for at, name in members
        if request.specified_parameters() <= auto._member(built[at]).supported_parameters
    ], unbuilt


def _built(auto: Any) -> tuple[list[int], dict[str, str]]:
    """The positions of the members ``auto`` can build, and why each other member cannot be built.

    Built as ``auto`` builds them, and cached there for the call that tries one:
    a member that is not configured -- no credential and no keyless tier, a
    setting missing -- or whose extra is not installed, refuses to be built and
    is no candidate. Extract's
    ``_eligible`` reads only the format, so its other options are checked
    against the built member.
    """
    built: list[int] = []
    unbuilt: dict[str, str] = {}
    for position, (name, _) in enumerate(auto._chain):
        try:
            auto._member(position)
        except ConfigurationError:
            unbuilt.setdefault(name, MSG_DROPPED_NOT_CONFIGURED)
        except ProviderNotInstalledError:
            unbuilt.setdefault(name, MSG_DROPPED_NOT_INSTALLED)
        else:
            built.append(position)
    return built, unbuilt
