"""Extract ``auto``'s options: it accepts what some member maps, and tries only a member that maps what is set."""

from __future__ import annotations

import pytest

from tests.test_auto import _extract_member, _Recorder, extract_chain_members, recorder
from web_router.constants import (
    AUTO_EXTRACT_APPENDED,
    AUTO_EXTRACT_FORMATS,
    AUTO_EXTRACT_KEYLESS,
    AUTO_EXTRACT_PREFERRED,
)
from web_router.exceptions import UnsupportedFormatError, UnsupportedParameterError
from web_router.providers.auto import AutoExtractProvider
from web_router.registry import get_extract_provider_class
from web_router.types import EXTRACT_PARAMETERS, ExtractRequest

__all__ = ["extract_chain_members", "recorder"]  # fixtures, re-exported so pytest finds them here


def test_auto_offers_exactly_the_formats_some_extract_member_returns() -> None:
    """The union: a format is routed to a member that returns it, so none has to be given up for the weakest."""
    members = [name for name, _ in AUTO_EXTRACT_KEYLESS] + list(AUTO_EXTRACT_PREFERRED) + list(AUTO_EXTRACT_APPENDED)
    offered = frozenset().union(*(get_extract_provider_class(name).supported_formats for name in members))

    assert offered == AUTO_EXTRACT_FORMATS
    mapped = frozenset().union(*(get_extract_provider_class(name).supported_parameters for name in members))
    assert mapped == EXTRACT_PARAMETERS, "auto accepts exactly what some member maps, as search's does"


def test_a_profile_extract_skips_to_the_member_that_maps_it(extract_chain_members: _Recorder) -> None:
    """Never dropped: a fetch that quietly ran anonymously would read as the login having failed."""
    _extract_member(extract_chain_members, "first", supported=frozenset({"format"}))
    _extract_member(extract_chain_members, "second")

    provider = AutoExtractProvider(chain=(("first", {}), ("second", {})))
    try:
        response = provider.extract(ExtractRequest(urls=["https://example.com"], profile="ctx-1"))
    finally:
        provider.close()

    assert extract_chain_members.called == ["second"]
    assert response.provider == "second"
    assert set(provider._members) == {1}, "the member that cannot read a profile was never built"


def test_a_profile_no_member_maps_is_rejected_before_any_member_is_tried(extract_chain_members: _Recorder) -> None:
    _extract_member(extract_chain_members, "first", supported=frozenset({"format"}))
    _extract_member(extract_chain_members, "second", supported=frozenset({"format"}), formats=frozenset({"text"}))

    provider = AutoExtractProvider(chain=(("first", {}), ("second", {})))
    try:
        with pytest.raises(UnsupportedParameterError) as caught:
            provider.extract(ExtractRequest(urls=["https://example.com"], profile="ctx-1"))
    finally:
        provider.close()

    message = str(caught.value)
    assert caught.value.parameters == ["profile"]
    assert "first (does not map 'profile')" in message
    assert "second (returns only 'text'; does not map 'profile')" in message, "every reason a member was passed over"
    assert "provider_options" not in message, "auto has no provider_options to point at"
    assert extract_chain_members.called == []
    assert provider._members == {}


def test_a_profile_mapped_only_where_the_format_is_not_blames_the_format(extract_chain_members: _Recorder) -> None:
    """The member that reads profiles is in the chain; telling the caller to add one would send them the wrong way."""
    _extract_member(extract_chain_members, "first", supported=frozenset({"format"}))
    _extract_member(extract_chain_members, "second", formats=frozenset({"markdown", "html"}))

    provider = AutoExtractProvider(chain=(("first", {}), ("second", {})))
    try:
        with pytest.raises(UnsupportedFormatError) as caught:
            provider.extract(ExtractRequest(urls=["https://example.com"], format="text", profile="ctx-1"))
    finally:
        provider.close()

    assert "second (returns only 'html', 'markdown')" in str(caught.value)
    assert extract_chain_members.called == []
