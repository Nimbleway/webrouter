from __future__ import annotations

import pytest

from web_router.exceptions import ProviderNotFoundError
from web_router.registry import (
    available_browse_providers,
    available_browser_providers,
    available_extract_providers,
    available_providers,
    available_research_providers,
    available_template_providers,
    create_provider,
    create_research_provider,
    create_template_provider,
    get_provider_class,
    get_research_provider_class,
    get_template_provider_class,
)
from web_router.registry_answer import available_answer_providers
from web_router.types import Provider, provider_name


def test_nimble_is_a_builtin_provider() -> None:
    assert "nimble" in available_providers()


def test_unknown_provider_is_reported_with_the_known_names() -> None:
    with pytest.raises(ProviderNotFoundError, match="nimble"):
        get_provider_class("does-not-exist")


def test_registered_provider_is_resolved_and_instantiated(fake_provider: type) -> None:
    assert get_provider_class("fake") is fake_provider
    assert isinstance(create_provider("fake"), fake_provider)


def test_provider_lookup_is_case_insensitive(fake_provider: type) -> None:
    assert get_provider_class("FAKE") is fake_provider


def test_the_enum_and_its_string_resolve_to_the_same_provider() -> None:
    assert get_provider_class(Provider.NIMBLE) is get_provider_class("nimble")


def test_every_enum_member_is_a_registered_provider() -> None:
    """The enum is the discoverable face of the registries; it must not drift from them.

    Against the union, because a name serves some methods and not others: `brave`
    searches and cannot extract, `oxylabs` extracts and cannot search, `steel`
    only opens browser sessions, and `browser-use` and `skyvern` only run browse
    tasks.
    """
    registered = (
        set(available_providers())
        | set(available_extract_providers())
        | set(available_template_providers())
        | set(available_research_providers())
        | set(available_answer_providers())
        | set(available_browser_providers())
        | set(available_browse_providers())
    )
    assert {member.value for member in Provider} <= registered


def test_a_custom_provider_is_still_registered_and_removed_by_string(fake_provider: type) -> None:
    """Custom backends have no enum member, which is why strings stay first-class."""
    assert get_provider_class("fake") is fake_provider


def test_an_enum_member_renders_as_its_plain_name() -> None:
    """It reaches users through error messages and SearchResponse.provider."""
    assert f"{Provider.NIMBLE}" == "nimble"
    assert provider_name(Provider.NIMBLE) == "nimble"


def test_nimble_is_a_builtin_research_provider() -> None:
    assert "nimble" in available_research_providers()


def test_registered_research_provider_is_resolved_and_instantiated(fake_research_provider: type) -> None:
    assert get_research_provider_class("fake") is fake_research_provider
    assert isinstance(create_research_provider("fake"), fake_research_provider)


def test_research_provider_lookup_is_case_insensitive(fake_research_provider: type) -> None:
    assert get_research_provider_class("FAKE") is fake_research_provider


def test_a_search_only_backend_is_unknown_to_research() -> None:
    """Each method answers for its own table, and says which names that table holds."""
    with pytest.raises(ProviderNotFoundError, match="nimble") as caught:
        get_research_provider_class("brave")

    assert "brave" not in caught.value.available


def test_the_three_registries_are_separate_tables() -> None:
    """A name serves the methods it serves; the registries are not copies of each other."""
    assert set(available_research_providers()) < set(available_providers()) | set(available_extract_providers())


def test_nimble_is_a_builtin_template_provider() -> None:
    assert available_template_providers() == ["nimble"], "no auto: a template name belongs to one vendor"


def test_registered_template_provider_is_resolved_and_instantiated(fake_template_provider: type) -> None:
    assert get_template_provider_class("FAKE") is fake_template_provider
    assert isinstance(create_template_provider("fake"), fake_template_provider)


def test_an_extract_only_backend_is_unknown_to_templates() -> None:
    """Jina extracts; it holds no template catalogue, and the table says which names do."""
    with pytest.raises(ProviderNotFoundError, match="nimble") as caught:
        get_template_provider_class("jina")

    assert "jina" not in caught.value.available
