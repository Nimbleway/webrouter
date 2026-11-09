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
from web_router.mcp.env import EnvSources
from web_router.mcp.env_credentials import ALL_ENV_TABLES, credential_variables
from web_router.mcp.methods import METHODS

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"

NOT_CREDENTIALS = {ENV_OPENAI_API_KEY, ENV_ANTHROPIC_API_KEY, ENV_LOCAL_BROWSER_PATH}
"""The judges' keys belong to ``evaluate()``, and ``CHROME_PATH`` is a path a foreign file must not aim."""


def _example_variables() -> set[str]:
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", ENV_EXAMPLE.read_text(encoding="utf-8"), flags=re.MULTILINE))


def test_every_credential_in_env_example_loads_and_nothing_else_does() -> None:
    assert _example_variables() - NOT_CREDENTIALS == set(credential_variables())


def test_the_tools_read_the_same_tables_the_loader_does() -> None:
    """``unlock`` and the no-backend hints name only variables a ``.env`` can actually set."""
    in_tools = {id(method.env) for method in METHODS.values()}
    assert in_tools == {id(table) for table in ALL_ENV_TABLES}


@pytest.mark.parametrize("name", [ENV_BRAVE_ANSWER_API_KEY, ENV_JINA_API_KEY])
def test_an_answer_only_key_and_an_optional_key_load_from_a_file(isolated_home: Path, name: str) -> None:
    env_file = isolated_home / "keys.env"
    env_file.write_text(f"{name}=not-a-real-key\n", encoding="utf-8")

    assert EnvSources().load(str(env_file)).loaded == (name,)
