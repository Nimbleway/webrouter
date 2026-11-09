"""Which members of extract ``auto`` can serve a request, before any is tried.

Kept apart from :mod:`web_router.providers.auto`, which calls it from
``AutoExtractProvider._eligible``, so that file stops growing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from web_router.constants import (
    MSG_AUTO_EXTRACT_NO_MEMBER,
    MSG_AUTO_EXTRACT_UNMAPPED,
    MSG_AUTO_SKIPPED,
    MSG_AUTO_UNFORMATTED,
    MSG_AUTO_UNMAPPED,
)
from web_router.exceptions import ProviderNotInstalledError, UnsupportedFormatError, UnsupportedParameterError
from web_router.providers._chain import _quoted
from web_router.types import ExtractFormat, ExtractRequest


def extract_eligible(
    name: str, chain: Sequence[tuple[str, Any]], wanted_format: ExtractFormat, request: ExtractRequest
) -> list[tuple[int, str]]:
    """The chain members that return the format and map every other option set, in order.

    Read from the members' classes as search's are. A member whose extra is
    not installed cannot be read, and is kept.

    Raises:
        UnsupportedParameterError: if an option the request sets -- ``profile``
            -- is mapped by no member that could otherwise serve it, naming
            what each one lacked. Nothing has been fetched.
        UnsupportedFormatError: if no member returns the format, naming what
            each one returns instead.
    """
    from web_router.registry import get_extract_provider_class

    wanted = request.specified_parameters() - {"format"}
    eligible: list[tuple[int, str]] = []
    skipped: list[str] = []
    returned: frozenset[ExtractFormat] = frozenset()
    widest: frozenset[str] = frozenset()
    for position, (provider_name, _) in enumerate(chain):
        try:
            member_cls = get_extract_provider_class(provider_name)
        except ProviderNotInstalledError:
            eligible.append((position, provider_name))
            continue
        returned |= member_cls.supported_formats
        widest |= member_cls.supported_parameters
        reasons: list[str] = []
        if wanted_format not in member_cls.supported_formats:
            reasons.append(MSG_AUTO_UNFORMATTED.format(formats=_quoted(member_cls.supported_formats)))
        lacks = wanted - member_cls.supported_parameters
        if lacks:
            reasons.append(MSG_AUTO_UNMAPPED.format(names=_quoted(lacks)))
        if not reasons:
            eligible.append((position, provider_name))
            continue
        skipped.append(MSG_AUTO_SKIPPED.format(name=provider_name, reasons="; ".join(reasons)))

    if eligible or not skipped:
        return eligible
    if wanted - widest:
        # No member maps the option at all, so it is the thing to drop. When
        # one does but cannot return the format, the format is the gap, and
        # the skipped list below says which member maps what.
        detail = MSG_AUTO_EXTRACT_UNMAPPED.format(skipped=", ".join(dict.fromkeys(skipped)))
        raise UnsupportedParameterError(name, wanted - widest, detail=detail)
    detail = MSG_AUTO_EXTRACT_NO_MEMBER.format(skipped=", ".join(dict.fromkeys(skipped)))
    raise UnsupportedFormatError(name, wanted_format, returned, detail=detail)
