"""Which variables a ``.env`` may set: every credential ``.env.example`` names, from one list of tables."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from web_router.constants import (
    ENV_ANTHROPIC_API_KEY,
    ENV_BRAVE_ANSWER_API_KEY,
    ENV_JINA_API_KEY,
    ENV_LOCAL_BROWSER_PATH,
    ENV_OPENAI_API_KEY,
)
from web_router.constants_litellm import ENV_LITELLM_PROXY_API_BASE
from web_router.constants_remote import ENV_REMOTE_ROUTER_API_KEY, ENV_REMOTE_ROUTER_URL
from web_router.mcp.env import EnvSources
from web_router.mcp.env_credentials import ALL_ENV_TABLES, credential_variables
from web_router.mcp.methods import METHODS
from web_router.vendors.catalog import get_catalog

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"

NOT_CREDENTIALS = {
    ENV_OPENAI_API_KEY,
    ENV_ANTHROPIC_API_KEY,
    ENV_LOCAL_BROWSER_PATH,
    ENV_REMOTE_ROUTER_API_KEY,
    ENV_REMOTE_ROUTER_URL,
    ENV_LITELLM_PROXY_API_BASE,
}
"""The judges' keys belong to ``evaluate()``, ``CHROME_PATH`` is a path a foreign file must not aim, and the remote
router's URL and key, and a LiteLLM proxy's URL, are read from the server's process environment only, never loaded
from a file."""


def test_the_remote_router_variables_are_never_credentials() -> None:
    assert not {ENV_REMOTE_ROUTER_API_KEY, ENV_REMOTE_ROUTER_URL} & set(credential_variables())


def _example_variables() -> set[str]:
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", ENV_EXAMPLE.read_text(encoding="utf-8"), flags=re.MULTILINE))


def test_every_credential_in_env_example_loads_and_nothing_else_does() -> None:
    # A vendor's aliases and settings load too, but its section lists only the first name and comments settings out.
    vendors = {name for vendor in get_catalog().all() for name in vendor.credential_variables()}
    listed = _example_variables() - NOT_CREDENTIALS
    assert listed - vendors == set(credential_variables()) - vendors
    assert listed & vendors <= set(credential_variables())


def test_the_tools_read_the_same_tables_the_loader_does() -> None:
    """``unlock`` and the no-backend hints name only variables a ``.env`` can actually set."""
    in_tools = {id(method.env) for method in METHODS.values()}
    assert in_tools == {id(table) for table in ALL_ENV_TABLES}


@pytest.mark.parametrize("name", [ENV_BRAVE_ANSWER_API_KEY, ENV_JINA_API_KEY])
def test_an_answer_only_key_and_an_optional_key_load_from_a_file(isolated_home: Path, name: str) -> None:
    env_file = isolated_home / "keys.env"
    env_file.write_text(f"{name}=not-a-real-key\n", encoding="utf-8")

    assert EnvSources().load(str(env_file)).loaded == (name,)


def test_a_bad_vendor_catalog_folder_does_not_stop_startup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The env loader still knows every backend variable; the vendor tools report the bad folder when called."""
    from web_router.constants_vendors import VENDOR_ENV_CATALOG_DIR
    from web_router.exceptions import ConfigurationError

    (tmp_path / "x.json").write_text("{not json", encoding="utf-8")
    for folder in (tmp_path / "missing", tmp_path):
        monkeypatch.setenv(VENDOR_ENV_CATALOG_DIR, str(folder))
        assert "TAVILY_API_KEY" in credential_variables()
        EnvSources()
        with pytest.raises(ConfigurationError):
            get_catalog()
