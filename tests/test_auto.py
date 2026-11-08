"""The auto providers: a chain of backends per method, routed per request, first answer wins."""

from __future__ import annotations

import inspect
from types import MappingProxyType
from typing import Any

import pytest

from web_router import AsyncWebRouter, WebRouter
from web_router._chains import chains
from web_router.constants import (
    AUTO_APPENDED,
    AUTO_CLIENT_OPTION,
    AUTO_CLIENT_PROVIDERS,
    AUTO_EXTRACT_APPENDED,
    AUTO_EXTRACT_DEFAULT_FORMAT,
    AUTO_EXTRACT_FORMATS,
    AUTO_EXTRACT_KEYLESS,
    AUTO_EXTRACT_PREFERRED,
    AUTO_KEYLESS,
    AUTO_PREFERRED,
    AUTO_SUPPORTED_PARAMETERS,
    ENV_BRAVE_API_KEY,
    ENV_BY_PROVIDER,
    ENV_EXA_API_KEY,
    ENV_FIRECRAWL_API_KEY,
    ENV_KEENABLE_API_KEY,
    ENV_PERPLEXITY_API_KEY,
    ENV_TAVILY_API_KEY,
    EXTRACT_ENV_BY_PROVIDER,
    PROVIDER_BRAVE,
    PROVIDER_EXA,
    PROVIDER_EXA_MCP,
    PROVIDER_FIRECRAWL,
    PROVIDER_JINA,
    PROVIDER_KEENABLE,
    PROVIDER_NIMBLE,
    PROVIDER_PARALLEL_MCP,
    PROVIDER_PERPLEXITY,
    PROVIDER_TAVILY,
    PROVIDER_YOU_MCP,
    RESEARCH_ENV_BY_PROVIDER,
)
from web_router.exceptions import (
    ConfigurationError,
    ProviderError,
    ProviderNotInstalledError,
    UnsupportedFormatError,
    UnsupportedParameterError,
)
from web_router.providers.auto import AutoExtractProvider, AutoProvider, AutoResearchProvider
from web_router.registry import (
    _BUILTIN_PROVIDERS,
    available_extract_providers,
    available_providers,
    available_research_providers,
    get_extract_provider_class,
    get_provider_class,
    get_research_provider_class,
    register_extract_provider,
    register_provider,
    unregister_extract_provider,
    unregister_provider,
)
from web_router.types import (
    EXTRACT_PARAMETERS,
    NORMALIZED_PARAMETERS,
    ExtractedDocument,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

_EVERY_SEARCH_KEY = [env_var for env_var in ENV_BY_PROVIDER.values() if env_var]
_EVERY_RESEARCH_KEY = sorted({env_var for env_vars in RESEARCH_ENV_BY_PROVIDER.values() for env_var in env_vars})
_EVERY_EXTRACT_KEY = sorted({env_var for env_vars in EXTRACT_ENV_BY_PROVIDER.values() for env_var in env_vars})


class _Recorder:
    """Tracks which members a chain actually reached, and every name registered for it."""

    def __init__(self) -> None:
        self.called: list[str] = []
        # Tracked rather than listed in the fixture, so a member a test invents
        # -- or a builtin name it stands in for -- is always unregistered.
        self.registered: list[str] = []


@pytest.fixture
def recorder() -> _Recorder:
    return _Recorder()


def _member(
    recorder: _Recorder,
    member_name: str,
    raises: Exception | None = None,
    *,
    supported: frozenset[str] = NORMALIZED_PARAMETERS,
    empty: bool = False,
    answer: str | None = None,
) -> type:
    """Register a fake chain member that answers, or fails in a given way, mapping ``supported``.

    ``empty`` answers with no results, beside ``answer`` when one is given.
    """
    from tests.conftest import FakeProvider

    class Member(FakeProvider):  # type: ignore[misc, valid-type]
        name = member_name
        supported_parameters = supported

        def _respond(self, request: SearchRequest) -> SearchResponse:
            recorder.called.append(member_name)
            if raises is not None:
                raise raises
            return SearchResponse(
                provider=member_name,
                query=request.query,
                results=[] if empty else [SearchResult(url=f"https://{member_name}.example", content="hi")],
                answer=answer,
            )

    register_provider(member_name, Member)
    recorder.registered.append(member_name)
    return Member


@pytest.fixture
def chain_members(recorder: _Recorder) -> Any:
    yield recorder
    for name in recorder.registered:
        unregister_provider(name)


def test_the_first_member_that_answers_wins(chain_members: _Recorder) -> None:
    _member(chain_members, "first")
    _member(chain_members, "second")

    provider = AutoProvider(chain=(("first", {}), ("second", {})))
    try:
        response = provider.search(SearchRequest(query="hi"))
    finally:
        provider.close()

    assert chain_members.called == ["first"], "the second member should never have been reached"
    assert response.results


def test_a_failing_member_falls_through_to_the_next(chain_members: _Recorder) -> None:
    _member(chain_members, "first", raises=ProviderError("first", "rate limited"))
    _member(chain_members, "second")

    provider = AutoProvider(chain=(("first", {}), ("second", {})))
    try:
        response = provider.search(SearchRequest(query="hi"))
    finally:
        provider.close()

    assert chain_members.called == ["first", "second"]
    assert response.provider == "second"


def test_the_response_names_the_backend_that_answered(chain_members: _Recorder) -> None:
    """A caller must be able to tell who served the query -- never just "auto"."""
    _member(chain_members, "first")

    provider = AutoProvider(chain=(("first", {}),))
    try:
        response = provider.search(SearchRequest(query="hi"))
    finally:
        provider.close()

    assert response.provider == "first"


def test_a_missing_extra_is_recoverable(chain_members: _Recorder) -> None:
    """An uninstalled member must not sink the chain -- the next one may be installed."""
    _member(chain_members, "first", raises=ProviderNotInstalledError("first", "first"))
    _member(chain_members, "second")

    provider = AutoProvider(chain=(("first", {}), ("second", {})))
    try:
        assert provider.search(SearchRequest(query="hi")).provider == "second"
    finally:
        provider.close()


def test_a_missing_credential_is_recoverable(chain_members: _Recorder) -> None:
    _member(chain_members, "first", raises=ConfigurationError.missing_api_key("first", "FIRST_KEY"))
    _member(chain_members, "second")

    provider = AutoProvider(chain=(("first", {}), ("second", {})))
    try:
        assert provider.search(SearchRequest(query="hi")).provider == "second"
    finally:
        provider.close()


def test_exhausting_the_chain_reports_every_reason(chain_members: _Recorder) -> None:
    """One error naming only "auto" would hide which backend failed and why."""
    _member(chain_members, "first", raises=ProviderError("first", "rate limited"))
    _member(chain_members, "second", raises=ProviderError("second", "upstream 503"))

    provider = AutoProvider(chain=(("first", {}), ("second", {})))
    try:
        with pytest.raises(ProviderError) as caught:
            provider.search(SearchRequest(query="hi"))
    finally:
        provider.close()

    message = str(caught.value)
    assert "first" in message and "rate limited" in message
    assert "second" in message and "upstream 503" in message


def test_an_option_no_member_maps_is_rejected_before_any_member_is_tried(chain_members: _Recorder) -> None:
    """Falling through on a caller error would run a weaker query on every backend."""
    _member(chain_members, "first", supported=frozenset({"max_results"}))
    _member(chain_members, "second", supported=frozenset({"max_results", "search_depth"}))

    provider = AutoProvider(chain=(("first", {}), ("second", {})))
    try:
        with pytest.raises(UnsupportedParameterError) as caught:
            provider.search(SearchRequest(query="hi", country="US", search_depth="deep"))
    finally:
        provider.close()

    message = str(caught.value)
    assert caught.value.parameters == ["country"], "the option no member maps at all is the one to drop"
    assert "first (does not map 'country', 'search_depth')" in message
    assert "second (does not map 'country')" in message
    assert "provider_options" not in message, "auto has no provider_options to point at"
    assert chain_members.called == []
    assert provider._members == {}, "a member passed over is never even built"


def test_options_mapped_only_by_different_members_are_all_named(chain_members: _Recorder) -> None:
    _member(chain_members, "first", supported=frozenset({"search_depth"}))
    _member(chain_members, "second", supported=frozenset({"country"}))

    provider = AutoProvider(chain=(("first", {}), ("second", {})))
    try:
        with pytest.raises(UnsupportedParameterError) as caught:
            provider.search(SearchRequest(query="hi", country="US", search_depth="deep"))
    finally:
        provider.close()

    assert caught.value.parameters == ["country", "search_depth"]
    assert chain_members.called == []


def test_a_member_that_does_not_map_an_option_is_passed_over_unbuilt(chain_members: _Recorder) -> None:
    """The union surface is safe because each request goes only where every option it sets is mapped."""
    _member(chain_members, "first", supported=frozenset({"max_results"}))
    _member(chain_members, "second", supported=frozenset({"max_results", "search_depth"}))

    provider = AutoProvider(chain=(("first", {}), ("second", {})))
    try:
        deep = provider.search(SearchRequest(query="hi", search_depth="deep"))
        plain = provider.search(SearchRequest(query="hi"))
    finally:
        provider.close()

    assert deep.provider == "second"
    assert plain.provider == "first", "a request the first member can serve still goes to it first"
    assert chain_members.called == ["second", "first"]


async def test_a_member_is_passed_over_the_same_way_async(chain_members: _Recorder) -> None:
    _member(chain_members, "first", supported=frozenset({"max_results"}))
    _member(chain_members, "second", raises=ProviderError("second", "rate limited"))
    _member(chain_members, "third")

    provider = AutoProvider(chain=(("first", {}), ("second", {}), ("third", {})))
    try:
        response = await provider.asearch(SearchRequest(query="hi", include_domains=["example.com"]))
    finally:
        await provider.aclose()

    assert chain_members.called == ["second", "third"], "the members tried still fall through on failure"
    assert response.provider == "third"


def test_a_member_whose_extra_is_missing_is_still_fallen_through(
    chain_members: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its class cannot be read, so it is tried and fails as a missing extra always has."""
    monkeypatch.setitem(_BUILTIN_PROVIDERS, "ghost", ("ghost_sdk_that_is_not_installed", "Ghost", "search"))
    _member(chain_members, "first")

    provider = AutoProvider(chain=(("ghost", {}), ("first", {})))
    try:
        response = provider.search(SearchRequest(query="hi", search_depth="deep"))
    finally:
        provider.close()

    assert response.provider == "first"


def test_members_are_built_once_and_closed(chain_members: _Recorder) -> None:
    _member(chain_members, "first")

    provider = AutoProvider(chain=(("first", {}),))
    provider.search(SearchRequest(query="hi"))
    provider.search(SearchRequest(query="again"))
    built = list(provider._members.values())
    provider.close()

    assert len(built) == 1, "the member should be cached, not rebuilt per search"
    assert all(member.closed for member in built)


async def test_the_chain_works_the_same_way_async(chain_members: _Recorder) -> None:
    _member(chain_members, "first", raises=ProviderError("first", "rate limited"))
    _member(chain_members, "second")

    provider = AutoProvider(chain=(("first", {}), ("second", {})))
    try:
        response = await provider.asearch(SearchRequest(query="hi"))
    finally:
        await provider.aclose()

    assert chain_members.called == ["first", "second"]
    assert response.provider == "second"


def test_auto_declares_every_option_some_member_maps() -> None:
    """At least the union, so no member's filters are lost to the weakest one; every shared option, in fact.

    Safe because a member is passed over for any option it does not map; see
    `test_a_member_that_does_not_map_an_option_is_passed_over_unbuilt`.
    """
    members = [name for name, _ in AUTO_KEYLESS] + list(AUTO_PREFERRED) + list(AUTO_APPENDED)
    mapped = frozenset().union(*(get_provider_class(name).supported_parameters for name in members))

    assert mapped <= AUTO_SUPPORTED_PARAMETERS


def test_every_search_backend_is_a_member(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each keyed backend joins, in the order make chains generated, ranked ones ahead of the keyless tier."""
    for env_var in _EVERY_SEARCH_KEY:
        monkeypatch.setenv(env_var, "test-key")

    chain = [name for name, _ in AutoProvider.default_chain()]

    generated = chains()["search"]
    assert chain[: len(generated["preferred"])] == generated["preferred"], "the order is make chains', not a literal"
    assert chain[-len(generated["appended"]) :] == generated["appended"]
    # Tavily and Keenable are held with a key here, so their keyless entries would only repeat the keyed request.
    keyless = [name for name, _ in AUTO_KEYLESS if name not in {*AUTO_PREFERRED, *AUTO_APPENDED}]
    assert chain == [*AUTO_PREFERRED, *keyless, *AUTO_APPENDED]
    keyed = {name for name, env_var in ENV_BY_PROVIDER.items() if env_var}
    assert set(AUTO_PREFERRED) | set(AUTO_APPENDED) == keyed


def test_a_keyed_provider_is_preferred_over_an_anonymous_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key in the environment says which backend you want; auto must not ignore it."""
    for env_var in _EVERY_SEARCH_KEY:
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv(ENV_BRAVE_API_KEY, "test-key")
    monkeypatch.setenv(ENV_PERPLEXITY_API_KEY, "test-key")

    chain = [name for name, _ in AutoProvider.default_chain()]

    # Every key comes before the free tier, in the scan's order: Perplexity ahead of Brave.
    assert chain == [
        PROVIDER_PERPLEXITY,
        PROVIDER_BRAVE,
        PROVIDER_TAVILY,
        PROVIDER_YOU_MCP,
        PROVIDER_EXA_MCP,
        PROVIDER_PARALLEL_MCP,
        PROVIDER_KEENABLE,
    ]


def test_with_no_credentials_the_chain_is_the_keyless_one(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_var in _EVERY_SEARCH_KEY:
        monkeypatch.delenv(env_var, raising=False)

    assert AutoProvider.default_chain() == AUTO_KEYLESS


def _stand_ins(recorder: _Recorder, *names: str, supported: frozenset[str]) -> None:
    """Replace builtin backends with fakes of the same name, mapping what the real ones do, so nothing is sent."""
    for name in names:
        _member(recorder, name, supported=supported)


def test_a_failing_keyed_member_falls_through_to_the_free_tier(
    chain_members: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    for env_var in _EVERY_SEARCH_KEY:
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv(ENV_PERPLEXITY_API_KEY, "test-key")
    _member(
        chain_members,
        PROVIDER_PERPLEXITY,
        ProviderError(PROVIDER_PERPLEXITY, "out of credit"),
        supported=get_provider_class(PROVIDER_PERPLEXITY).supported_parameters,
    )
    _stand_ins(chain_members, PROVIDER_TAVILY, supported=AUTO_SUPPORTED_PARAMETERS)

    with WebRouter() as router:
        response = router.search("hi")

    assert response.provider == PROVIDER_TAVILY
    assert chain_members.called == [PROVIDER_PERPLEXITY, PROVIDER_TAVILY]


def test_a_key_held_for_any_search_backend_serves_before_the_free_tier(
    chain_members: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Perplexity key, even one held for research, now serves plain searches: every key precedes the free tier."""
    for env_var in _EVERY_SEARCH_KEY:
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv(ENV_PERPLEXITY_API_KEY, "test-key")
    _stand_ins(
        chain_members, PROVIDER_TAVILY, PROVIDER_YOU_MCP, PROVIDER_PERPLEXITY, supported=AUTO_SUPPORTED_PARAMETERS
    )

    with WebRouter() as router:
        response = router.search("hi")

    assert response.provider == PROVIDER_PERPLEXITY
    assert chain_members.called == [PROVIDER_PERPLEXITY]


def test_a_depth_request_skips_the_members_without_depth_for_the_keyless_tier(
    chain_members: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Perplexity and Brave have no depth control, so their keys cannot serve this request.

    They are read as the real classes they are, and never built; the keyless
    tier stands in for Tavily and You.com, both of which map depth.
    """
    for env_var in _EVERY_SEARCH_KEY:
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv(ENV_PERPLEXITY_API_KEY, "test-key")
    monkeypatch.setenv(ENV_BRAVE_API_KEY, "test-key")
    _stand_ins(chain_members, PROVIDER_TAVILY, PROVIDER_YOU_MCP, supported=frozenset({"max_results", "search_depth"}))

    with WebRouter() as router:
        response = router.search("hi", search_depth="deep")
        passed_over = router.get_provider()

    assert response.provider == PROVIDER_TAVILY
    assert chain_members.called == [PROVIDER_TAVILY]
    assert isinstance(passed_over, AutoProvider)
    assert passed_over.members == (
        PROVIDER_PERPLEXITY,
        PROVIDER_BRAVE,
        PROVIDER_TAVILY,
        PROVIDER_YOU_MCP,
        PROVIDER_EXA_MCP,
        PROVIDER_PARALLEL_MCP,
        PROVIDER_KEENABLE,
    )
    assert set(passed_over._members) == {2}, "perplexity and brave were never built"


def test_a_depth_request_only_depthless_members_could_take_names_each(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_var in _EVERY_SEARCH_KEY:
        monkeypatch.delenv(env_var, raising=False)

    router = WebRouter(provider_config={"auto": {"chain": [PROVIDER_PERPLEXITY, PROVIDER_BRAVE]}})
    with router, pytest.raises(UnsupportedParameterError) as caught:
        router.search("hi", search_depth="deep")

    message = str(caught.value)
    assert "perplexity (does not map 'search_depth')" in message
    assert "brave (does not map 'search_depth')" in message
    assert "nothing was searched" in message


def test_a_chain_in_provider_config_is_the_order_auto_tries(chain_members: _Recorder) -> None:
    """Names alone, or ``(name, kwargs)`` as before; either way the caller's order, not the default."""
    _member(chain_members, "first")
    _member(chain_members, "second")

    with WebRouter(provider_config={"auto": {"chain": ["second", ("first", {"timeout": 5})]}}) as router:
        response = router.search("hi")
        auto = router.get_provider()

    assert response.provider == "second"
    assert isinstance(auto, AutoProvider) and auto.members == ("second", "first")


def test_a_chain_keyed_by_method_sets_only_the_methods_it_names(chain_members: _Recorder) -> None:
    """provider_config["auto"] reaches every method's auto, so a list there is every method's chain."""
    _member(chain_members, "first")

    listed = WebRouter(provider_config={"auto": {"chain": ["first"]}})
    keyed = WebRouter(provider_config={"auto": {"chain": {"search": ["first"]}}})
    try:
        listed_extract = listed.get_extract_provider()
        keyed_search, keyed_extract = keyed.get_provider(), keyed.get_extract_provider()
    finally:
        listed.close()
        keyed.close()

    assert isinstance(listed_extract, AutoExtractProvider) and listed_extract.members == ("first",)
    assert isinstance(keyed_search, AutoProvider) and keyed_search.members == ("first",)
    assert isinstance(keyed_extract, AutoExtractProvider)
    assert keyed_extract.members == tuple(name for name, _ in AutoExtractProvider.default_chain())


@pytest.mark.parametrize(
    ("chain", "match"),
    [({"serach": ["nimble"]}, "no method 'serach'"), ("nimble", r"pass \['nimble'\]")],
    ids=["unknown-method", "bare-name"],
)
def test_a_malformed_chain_is_refused_rather_than_guessed(chain: Any, match: str) -> None:
    with pytest.raises(ConfigurationError, match=match):
        AutoProvider(chain=chain)


def test_the_same_provider_can_appear_twice_on_different_terms(chain_members: _Recorder) -> None:
    """Tavily keyed then Tavily keyless: caching by name would collapse the two."""
    _member(chain_members, "twice", raises=ProviderError("twice", "keyed attempt failed"))

    provider = AutoProvider(chain=(("twice", {}), ("twice", {"allow_keyless": True})))
    try:
        with pytest.raises(ProviderError):
            provider.search(SearchRequest(query="hi"))
    finally:
        provider.close()

    assert chain_members.called == ["twice", "twice"], "the second entry was skipped"
    assert len(provider._members) == 2


def test_nothing_installed_reports_one_fix_not_a_row_of_failures(chain_members: _Recorder) -> None:
    """Every member missing is a setup problem, so name the extra rather than list backends."""
    _member(chain_members, "first", raises=ProviderNotInstalledError("first", "first"))
    _member(chain_members, "second", raises=ProviderNotInstalledError("second", "second"))

    provider = AutoProvider(chain=(("first", {}), ("second", {})))
    try:
        with pytest.raises(ProviderNotInstalledError, match=r"--extra all"):
            provider.search(SearchRequest(query="hi"))
    finally:
        provider.close()


# -- The extract chain -------------------------------------------------------


def _extract_member(
    recorder: _Recorder,
    member_name: str,
    raises: Exception | None = None,
    *,
    formats: frozenset[ExtractFormat] = frozenset({"markdown", "html", "text"}),
    blank: frozenset[str] = frozenset(),
) -> type:
    """Register a fake extract chain member that answers, or fails in a given way, returning ``formats``.

    A URL in ``blank`` comes back as a document with no text.
    """
    from tests.conftest import FakeExtractProvider

    class Member(FakeExtractProvider):  # type: ignore[misc, valid-type]
        name = member_name
        supported_formats = formats
        default_format = sorted(formats)[0]

        def _respond(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            recorder.called.append(member_name)
            if raises is not None:
                raise raises
            return ExtractResponse(
                provider=member_name,
                documents=[
                    ExtractedDocument(url=url, content="" if url in blank else "hi", format=self.format_of(request))
                    for url in urls
                ],
            )

    register_extract_provider(member_name, Member)
    recorder.registered.append(member_name)
    return Member


@pytest.fixture
def extract_chain_members(recorder: _Recorder) -> Any:
    yield recorder
    for name in recorder.registered:
        unregister_extract_provider(name)


def test_the_first_extract_member_that_answers_wins(extract_chain_members: _Recorder) -> None:
    _extract_member(extract_chain_members, "first")
    _extract_member(extract_chain_members, "second")

    provider = AutoExtractProvider(chain=(("first", {}), ("second", {})))
    try:
        response = provider.extract(ExtractRequest(urls=["https://example.com"]))
    finally:
        provider.close()

    assert extract_chain_members.called == ["first"]
    assert response.documents


def test_a_failing_extract_member_falls_through_to_the_next(extract_chain_members: _Recorder) -> None:
    _extract_member(extract_chain_members, "first", raises=ProviderError("first", "rate limited"))
    _extract_member(extract_chain_members, "second")

    provider = AutoExtractProvider(chain=(("first", {}), ("second", {})))
    try:
        response = provider.extract(ExtractRequest(urls=["https://example.com"]))
    finally:
        provider.close()

    assert extract_chain_members.called == ["first", "second"]
    assert response.provider == "second", "the response must name the backend that answered"


def test_the_format_is_pinned_before_delegating(extract_chain_members: _Recorder) -> None:
    """Members default differently -- Nimble to HTML, Tavily to markdown.

    Letting the default fall through would make the shape of the content depend
    on which backend happened to answer, which is the one thing the package
    exists to prevent.
    """
    _extract_member(extract_chain_members, "first")

    provider = AutoExtractProvider(chain=(("first", {}),))
    try:
        response = provider.extract(ExtractRequest(urls=["https://example.com"]))
    finally:
        provider.close()

    assert response.documents[0].format == AUTO_EXTRACT_DEFAULT_FORMAT


def test_exhausting_the_extract_chain_reports_every_reason(extract_chain_members: _Recorder) -> None:
    _extract_member(extract_chain_members, "first", raises=ProviderError("first", "rate limited"))
    _extract_member(extract_chain_members, "second", raises=ProviderError("second", "upstream 503"))

    provider = AutoExtractProvider(chain=(("first", {}), ("second", {})))
    try:
        with pytest.raises(ProviderError) as caught:
            provider.extract(ExtractRequest(urls=["https://example.com"]))
    finally:
        provider.close()

    message = str(caught.value)
    assert "rate limited" in message and "upstream 503" in message


async def test_the_extract_chain_works_the_same_way_async(extract_chain_members: _Recorder) -> None:
    _extract_member(extract_chain_members, "first", raises=ProviderError("first", "rate limited"))
    _extract_member(extract_chain_members, "second")

    provider = AutoExtractProvider(chain=(("first", {}), ("second", {})))
    try:
        response = await provider.aextract(ExtractRequest(urls=["https://example.com"]))
    finally:
        await provider.aclose()

    assert extract_chain_members.called == ["first", "second"]
    assert response.provider == "second"


def test_auto_offers_exactly_the_formats_some_extract_member_returns() -> None:
    """The union: a format is routed to a member that returns it, so none has to be given up for the weakest."""
    members = [name for name, _ in AUTO_EXTRACT_KEYLESS] + list(AUTO_EXTRACT_PREFERRED) + list(AUTO_EXTRACT_APPENDED)
    offered = frozenset().union(*(get_extract_provider_class(name).supported_formats for name in members))

    assert offered == AUTO_EXTRACT_FORMATS
    for name in members:
        mapped = get_extract_provider_class(name).supported_parameters
        assert mapped >= EXTRACT_PARAMETERS, f"{name} cannot honour everything auto accepts"


def test_a_text_extract_skips_to_a_member_that_returns_text(extract_chain_members: _Recorder) -> None:
    """Never relabelled: the member that answers is one that really returns text."""
    _extract_member(extract_chain_members, "first", formats=frozenset({"markdown", "html"}))
    _extract_member(extract_chain_members, "second", formats=frozenset({"text"}))

    provider = AutoExtractProvider(chain=(("first", {}), ("second", {})))
    try:
        response = provider.extract(ExtractRequest(urls=["https://example.com"], format="text"))
    finally:
        provider.close()

    assert extract_chain_members.called == ["second"]
    assert response.provider == "second"
    assert response.documents[0].format == "text"
    assert set(provider._members) == {1}, "the member that cannot return text was never built"


async def test_an_unset_format_never_reaches_a_member_that_cannot_return_markdown(
    extract_chain_members: _Recorder,
) -> None:
    """The default is pinned before routing, so a text-only member such as Exa is passed over for it."""
    _extract_member(extract_chain_members, "first", formats=frozenset({"text"}))
    _extract_member(extract_chain_members, "second", formats=frozenset({"markdown"}))

    provider = AutoExtractProvider(chain=(("first", {}), ("second", {})))
    try:
        response = await provider.aextract(ExtractRequest(urls=["https://example.com"]))
    finally:
        await provider.aclose()

    assert extract_chain_members.called == ["second"]
    assert response.documents[0].format == AUTO_EXTRACT_DEFAULT_FORMAT


def test_a_format_no_member_returns_raises_naming_what_each_returns(extract_chain_members: _Recorder) -> None:
    _extract_member(extract_chain_members, "first", formats=frozenset({"markdown", "html"}))
    _extract_member(extract_chain_members, "second", formats=frozenset({"markdown"}))

    provider = AutoExtractProvider(chain=(("first", {}), ("second", {})))
    try:
        with pytest.raises(UnsupportedFormatError) as caught:
            provider.extract(ExtractRequest(urls=["https://example.com"], format="text"))
    finally:
        provider.close()

    message = str(caught.value)
    assert caught.value.requested == "text"
    assert caught.value.supported == ["html", "markdown"], "what this chain can return, not every format auto names"
    assert "first (returns only 'html', 'markdown')" in message
    assert "second (returns only 'markdown')" in message
    assert extract_chain_members.called == []


def test_every_extract_backend_is_a_member(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_var in _EVERY_EXTRACT_KEY:
        monkeypatch.setenv(env_var, "test-key")

    chain = [name for name, _ in AutoExtractProvider.default_chain()]

    assert chain[:5] == ["nimble", "tavily", "firecrawl", "oxylabs", "brightdata"], "the original order is kept"
    held = {*AUTO_EXTRACT_PREFERRED, *AUTO_EXTRACT_APPENDED}
    keyless = [name for name, _ in AUTO_EXTRACT_KEYLESS if name not in held]
    assert chain == [*AUTO_EXTRACT_PREFERRED, *keyless, *AUTO_EXTRACT_APPENDED]
    keyed = {name for name, env_vars in EXTRACT_ENV_BY_PROVIDER.items() if env_vars}
    assert set(AUTO_EXTRACT_PREFERRED) | set(AUTO_EXTRACT_APPENDED) == keyed


def test_a_keyed_extract_provider_is_preferred_over_an_anonymous_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_var in _EVERY_EXTRACT_KEY:
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv(ENV_EXA_API_KEY, "test-key")

    chain = [name for name, _ in AutoExtractProvider.default_chain()]

    assert chain == [PROVIDER_TAVILY, PROVIDER_JINA, PROVIDER_FIRECRAWL, PROVIDER_KEENABLE, PROVIDER_EXA]


def test_with_no_credentials_the_extract_chain_is_the_keyless_one(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_var in _EVERY_EXTRACT_KEY:
        monkeypatch.delenv(env_var, raising=False)

    assert AutoExtractProvider.default_chain() == AUTO_EXTRACT_KEYLESS


def test_an_extract_chain_in_provider_config_is_honoured(extract_chain_members: _Recorder) -> None:
    _extract_member(extract_chain_members, "first", formats=frozenset({"markdown"}))
    _extract_member(extract_chain_members, "second", formats=frozenset({"text"}))

    with WebRouter(provider_config={"auto": {"chain": {"extract": ["second", "first"]}}}) as router:
        text = router.extract("https://example.com", format="text")
        markdown = router.extract("https://example.com")

    assert (text.provider, markdown.provider) == ("second", "first")


def test_a_backend_held_with_a_key_is_not_tried_again_as_keyless(monkeypatch: pytest.MonkeyPatch) -> None:
    """A keyless member resolves the key when one is set, so a second try would repeat the same request."""
    for env_var in (*_EVERY_SEARCH_KEY, *_EVERY_EXTRACT_KEY):
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv(ENV_TAVILY_API_KEY, "test-key")
    monkeypatch.setenv(ENV_FIRECRAWL_API_KEY, "test-key")

    search = [name for name, _ in AutoProvider.default_chain()]
    extract = [name for name, _ in AutoExtractProvider.default_chain()]

    assert search == [PROVIDER_TAVILY, PROVIDER_YOU_MCP, PROVIDER_EXA_MCP, PROVIDER_PARALLEL_MCP, PROVIDER_KEENABLE]
    assert extract == [PROVIDER_TAVILY, PROVIDER_FIRECRAWL, PROVIDER_JINA, PROVIDER_KEENABLE]


def test_a_backend_appended_with_a_key_leaves_the_keyless_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keenable joined after the keyless tier: held with a key it is tried there, once, never early as "keyless"."""
    for env_var in (*_EVERY_SEARCH_KEY, *_EVERY_EXTRACT_KEY):
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv(ENV_KEENABLE_API_KEY, "test-key")

    search = [name for name, _ in AutoProvider.default_chain()]
    extract = [name for name, _ in AutoExtractProvider.default_chain()]

    assert search == [PROVIDER_TAVILY, PROVIDER_YOU_MCP, PROVIDER_EXA_MCP, PROVIDER_PARALLEL_MCP, PROVIDER_KEENABLE]
    assert extract == [PROVIDER_TAVILY, PROVIDER_JINA, PROVIDER_FIRECRAWL, PROVIDER_KEENABLE]
    assert dict(AutoProvider.default_chain())[PROVIDER_KEENABLE] == {}, "the keyed entry, not the keyless one"


def test_a_backend_s_provider_config_reaches_it_through_auto_and_the_chain_s_options_win(
    chain_members: _Recorder,
) -> None:
    """A key passed in code must reach a backend the same way through auto as by name."""
    built: list[dict[str, Any]] = []
    base = _member(chain_members, "recording")

    class Recording(base):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            built.append(dict(kwargs))
            super().__init__(**kwargs)

    register_provider("recording", Recording)
    config = {"recording": {"api_key": "from-code", "timeout": 5}}
    with WebRouter(provider_config={**config, "auto": {"chain": ["recording"]}}) as router:
        assert router.search("q").provider == "recording"
    with WebRouter(provider_config={**config, "auto": {"chain": [("recording", {"timeout": 9})]}}) as router:
        router.search("q")

    assert built == [{"api_key": "from-code", "timeout": 5}, {"api_key": "from-code", "timeout": 9}]


def test_a_key_given_in_code_puts_its_backend_in_auto_s_default_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_var in _EVERY_SEARCH_KEY:
        monkeypatch.delenv(env_var, raising=False)

    assert PROVIDER_PERPLEXITY not in AutoProvider().members
    with WebRouter(provider_config={PROVIDER_PERPLEXITY: {"api_key": "from-code"}}) as router:
        assert router.get_provider("auto").members[0] == PROVIDER_PERPLEXITY, "ranked as a key in the env is"
    with WebRouter(provider_config={PROVIDER_PERPLEXITY: {"timeout": 5}}) as router:
        assert PROVIDER_PERPLEXITY not in router.get_provider("auto").members, "config without a key adds nothing"


async def test_a_client_given_in_code_puts_nimble_in_auto_s_chain_and_serves_on_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller with no service key, only a per-user ``AsyncNimble``: ``auto`` still reaches Nimble on it."""
    nimble_python = pytest.importorskip("nimble_python", reason="nimble extra not installed")
    httpx = pytest.importorskip("httpx")
    for env_var in _EVERY_SEARCH_KEY:
        monkeypatch.delenv(env_var, raising=False)
    seen: list[Any] = []

    def handle(request: Any) -> Any:
        seen.append(request)
        return httpx.Response(200, json={"results": [{"url": "https://a.com", "title": "A"}]})

    client = nimble_python.AsyncNimble(
        api_key="end-user-key", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    async with AsyncWebRouter(provider_config={PROVIDER_NIMBLE: {"async_client": client}}) as router:
        assert router.get_provider("auto").members[0] == PROVIDER_NIMBLE
        response = await router.search("q")

    assert response.provider == PROVIDER_NIMBLE
    assert seen[0].headers["authorization"] == "Bearer end-user-key"
    assert not client.is_closed(), "the router closed its providers, and the caller's client is not one of them"


def test_a_client_given_to_a_backend_that_takes_none_adds_nothing_to_auto_s_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only Nimble's classes take ``async_client``; another backend would fail to start on it."""
    for env_var in _EVERY_SEARCH_KEY:
        monkeypatch.delenv(env_var, raising=False)

    assert PROVIDER_BRAVE not in AutoProvider(members_config={PROVIDER_BRAVE: {"async_client": object()}}).members
    assert PROVIDER_NIMBLE in AutoProvider(members_config={PROVIDER_NIMBLE: {"async_client": object()}}).members


@pytest.mark.parametrize("auto_class", [AutoProvider, AutoExtractProvider, AutoResearchProvider])
def test_a_nimble_client_given_in_code_puts_nimble_in_every_method_s_auto(
    monkeypatch: pytest.MonkeyPatch, auto_class: type[Any]
) -> None:
    for env_var in (*_EVERY_SEARCH_KEY, *_EVERY_EXTRACT_KEY, *_EVERY_RESEARCH_KEY):
        monkeypatch.delenv(env_var, raising=False)

    def members(auto: Any) -> list[str]:
        return [name for name, _ in auto._chain]

    assert PROVIDER_NIMBLE not in members(auto_class())
    assert members(auto_class(members_config={PROVIDER_NIMBLE: {"async_client": object()}}))[0] == PROVIDER_NIMBLE


def test_the_backends_whose_client_counts_are_exactly_those_whose_classes_take_one() -> None:
    """A class gaining ``async_client`` without joining the set would have its client ignored by auto."""
    pytest.importorskip("nimble_python", reason="nimble extra not installed")
    takes_a_client: set[str] = set()
    for available, get_class in (
        (available_providers, get_provider_class),
        (available_extract_providers, get_extract_provider_class),
        (available_research_providers, get_research_provider_class),
    ):
        for name in available():
            try:
                provider_class = get_class(name)
            except ProviderNotInstalledError:
                continue
            if AUTO_CLIENT_OPTION in inspect.signature(provider_class.__init__).parameters:
                takes_a_client.add(name)

    assert takes_a_client == AUTO_CLIENT_PROVIDERS


@pytest.mark.parametrize(
    "provider_config",
    [
        {PROVIDER_NIMBLE: {"async_client": object()}},
        {"auto": {"members_config": {"NIMBLE": {"async_client": object()}}}},
        {"auto": {"chain": [(PROVIDER_NIMBLE, {"async_client": object()}), PROVIDER_BRAVE]}},
        {"auto": {"chain": {"search": [(PROVIDER_NIMBLE, {"async_client": object()}), PROVIDER_BRAVE]}}},
        {
            "auto": {
                "chain": MappingProxyType(
                    {"search": [(PROVIDER_NIMBLE, MappingProxyType({"async_client": object()})), PROVIDER_BRAVE]}
                )
            }
        },
    ],
)
def test_a_sync_router_refuses_a_client_it_could_never_call_through(provider_config: dict[str, Any]) -> None:
    """Rather than let auto pass over Nimble unannounced on every call."""
    with pytest.raises(ConfigurationError, match="AsyncWebRouter"):
        WebRouter(provider_config=provider_config)


def test_a_sync_router_accepts_a_chain_that_hands_no_client() -> None:
    WebRouter(provider_config={"auto": {"chain": {"search": [PROVIDER_BRAVE, (PROVIDER_NIMBLE, {"timeout": 5})]}}})


def _nimble_client(seen: list[Any], response: dict[str, Any]) -> Any:
    """A caller's ``AsyncNimble`` on a mock transport that records each request and answers ``response``."""
    nimble_python = pytest.importorskip("nimble_python", reason="nimble extra not installed")
    httpx = pytest.importorskip("httpx")

    def handle(request: Any) -> Any:
        seen.append(request)
        return httpx.Response(200, json=response)

    return nimble_python.AsyncNimble(
        api_key="end-user-key", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )


async def test_extract_auto_serves_on_a_nimble_client_given_in_code(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_var in _EVERY_EXTRACT_KEY:
        monkeypatch.delenv(env_var, raising=False)
    seen: list[Any] = []
    page = {"status": "success", "url": "https://a.com", "data": {"markdown": "# A"}}
    client = _nimble_client(seen, page)

    async with AsyncWebRouter(provider_config={PROVIDER_NIMBLE: {"async_client": client}}) as router:
        response = await router.extract("https://a.com")

    assert response.provider == PROVIDER_NIMBLE
    assert response.documents[0].content == "# A"
    assert seen[0].headers["authorization"] == "Bearer end-user-key"
    assert not client.is_closed()


async def test_research_auto_starts_on_a_nimble_client_given_in_code(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_var in _EVERY_RESEARCH_KEY:
        monkeypatch.delenv(env_var, raising=False)
    seen: list[Any] = []
    run = {
        "id": "run-1",
        "web_search_agent_id": "agent-1",
        "status": "queued",
        "is_active": True,
        "effort": "medium",
        "interaction_id": "interaction-1",
        "created_at": "2026-01-01T00:00:00Z",
    }
    client = _nimble_client(seen, run)

    async with AsyncWebRouter(provider_config={PROVIDER_NIMBLE: {"async_client": client}}) as router:
        job = await router.submit_research("q")

    assert PROVIDER_NIMBLE in job.job_id
    assert seen and all(request.headers["authorization"] == "Bearer end-user-key" for request in seen)
    assert not client.is_closed()


@pytest.mark.parametrize("auto_class", [AutoProvider, AutoExtractProvider, AutoResearchProvider])
def test_auto_refuses_at_construction_a_member_given_a_key_beside_its_client(auto_class: type[Any]) -> None:
    """Built mid-call, the member's own refusal would only send the call to the next backend."""
    with pytest.raises(ConfigurationError, match="not both"):
        auto_class(members_config={PROVIDER_NIMBLE: {"async_client": object(), "api_key": "service-key"}})
    with pytest.raises(ConfigurationError, match="not both"):
        auto_class(
            chain=[(PROVIDER_NIMBLE, {"api_key": "service-key"})],
            members_config={PROVIDER_NIMBLE: {"async_client": object()}},
        )


def test_auto_leaves_a_key_beside_a_client_to_a_provider_that_is_not_nimble(chain_members: _Recorder) -> None:
    """A custom provider may take both for its own reasons; the conflict is Nimble's."""
    _member(chain_members, "custom")

    auto = AutoProvider(chain=["custom"], members_config={"custom": {"async_client": object(), "api_key": "k"}})

    assert auto.members == ("custom",)


def test_a_sync_router_still_takes_a_method_keyed_chain_left_to_its_default() -> None:
    WebRouter(provider_config={"auto": {"chain": {"search": None, "extract": [PROVIDER_JINA]}}})


def test_extract_auto_falls_through_a_backend_configured_in_code_that_fails(
    extract_chain_members: _Recorder,
) -> None:
    """A common pattern: Nimble first, Firecrawl when Nimble fails, with keys passed in code."""
    _extract_member(extract_chain_members, "first", ProviderError("first", "401 Invalid API key"))
    _extract_member(extract_chain_members, "second")
    chain = {"auto": {"chain": {"extract": ["first", "second"]}}}

    with WebRouter(provider_config={"first": {"api_key": "bad"}, **chain}) as router:
        assert router.extract("https://a.test").provider == "second"
    assert extract_chain_members.called == ["first", "second"]


def test_an_explicit_members_config_joins_the_sibling_entries_and_matches_any_spelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env_var in _EVERY_SEARCH_KEY:
        monkeypatch.delenv(env_var, raising=False)
    config = {
        "Brave": {"api_key": "sibling"},
        "auto": {"members_config": {"PERPLEXITY": {"api_key": "explicit"}, "brave": {"timeout": 5}}},
    }
    with WebRouter(provider_config=config) as router:
        auto = router.get_provider("auto")

    assert auto.members[:2] == (PROVIDER_PERPLEXITY, PROVIDER_BRAVE), "both keys count, whatever their spelling"
    assert auto._members_config[PROVIDER_BRAVE] == {"api_key": "sibling", "timeout": 5}


def test_a_default_chain_override_that_takes_nothing_still_works_with_a_key_in_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(AutoProvider, "default_chain", staticmethod(lambda: ((PROVIDER_BRAVE, {}),)))

    assert AutoProvider(members_config={PROVIDER_PERPLEXITY: {"api_key": "k"}}).members == (PROVIDER_BRAVE,)


# -- An empty answer falls through as a failure does --------------------------


def test_a_search_with_no_results_falls_through_to_the_next(chain_members: _Recorder) -> None:
    _member(chain_members, "first", empty=True)
    _member(chain_members, "second")

    provider = AutoProvider(chain=(("first", {}), ("second", {})))
    try:
        response = provider.search(SearchRequest(query="hi"))
    finally:
        provider.close()

    assert chain_members.called == ["first", "second"]
    assert response.provider == "second"


def test_a_search_with_an_answer_and_no_results_is_not_empty(chain_members: _Recorder) -> None:
    _member(chain_members, "first", empty=True, answer="42")
    _member(chain_members, "second")

    provider = AutoProvider(chain=(("first", {}), ("second", {})))
    try:
        response = provider.search(SearchRequest(query="hi"))
    finally:
        provider.close()

    assert chain_members.called == ["first"]
    assert response.answer == "42"


async def test_an_empty_search_falls_through_the_same_way_async(chain_members: _Recorder) -> None:
    _member(chain_members, "first", empty=True)
    _member(chain_members, "second")

    provider = AutoProvider(chain=(("first", {}), ("second", {})))
    try:
        response = await provider.asearch(SearchRequest(query="hi"))
    finally:
        await provider.aclose()

    assert response.provider == "second"


def test_an_extract_of_blank_documents_falls_through_to_the_next(extract_chain_members: _Recorder) -> None:
    _extract_member(extract_chain_members, "first", blank=frozenset({"https://example.com"}))
    _extract_member(extract_chain_members, "second")

    provider = AutoExtractProvider(chain=(("first", {}), ("second", {})))
    try:
        response = provider.extract(ExtractRequest(urls=["https://example.com"]))
    finally:
        provider.close()

    assert extract_chain_members.called == ["first", "second"]
    assert response.provider == "second"


def test_an_extract_with_one_document_of_text_is_kept(extract_chain_members: _Recorder) -> None:
    _extract_member(extract_chain_members, "first", blank=frozenset({"https://a.example"}))
    _extract_member(extract_chain_members, "second")

    provider = AutoExtractProvider(chain=(("first", {}), ("second", {})))
    try:
        response = provider.extract(ExtractRequest(urls=["https://a.example", "https://b.example"]))
    finally:
        provider.close()

    assert response.provider == "first" and "second" not in extract_chain_members.called


def test_when_every_member_is_empty_the_first_empty_answer_is_returned(chain_members: _Recorder) -> None:
    """An empty result is an answer, not a failure: it names who gave it rather than raising."""
    _member(chain_members, "first", empty=True)
    _member(chain_members, "second", empty=True)
    _member(chain_members, "third", raises=ProviderError("third", "rate limited"))

    provider = AutoProvider(chain=(("first", {}), ("second", {}), ("third", {})))
    try:
        response = provider.search(SearchRequest(query="hi"))
    finally:
        provider.close()

    assert chain_members.called == ["first", "second", "third"]
    assert response.provider == "first" and not response.results


@pytest.mark.parametrize("method", ["search", "extract"])
def test_fallback_on_empty_off_returns_the_first_empty_answer(
    chain_members: _Recorder, extract_chain_members: _Recorder, method: str
) -> None:
    _member(chain_members, "first", empty=True)
    _member(chain_members, "second")
    _extract_member(extract_chain_members, "first", blank=frozenset({"https://example.com"}))
    _extract_member(extract_chain_members, "second")

    config = {"auto": {"chain": ["first", "second"], "fallback_on_empty": False}}
    with WebRouter(provider_config=config) as router:
        response = router.search("hi") if method == "search" else router.extract("https://example.com")

    assert response.provider == "first"
