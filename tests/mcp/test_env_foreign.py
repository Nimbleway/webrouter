"""A remembered ``.env`` from another directory is loaded as before, but said out loud.

``load_env(remember=true)`` records a path in the user config directory, and
every later start reads it -- in any project. Keys from another checkout then
make a "keyless" install bill without anything looking different, so the
capabilities resource leads with a warning and the server instructions carry one.
"""

from __future__ import annotations

import os
from pathlib import Path

from tests.mcp.env_helpers import KEY, write
from web_router.constants import ENV_BRAVE_API_KEY, MCP_ENV_POINTER_FILE
from web_router.mcp.env import EnvSources, user_config_dir


def _remember(path: Path) -> None:
    EnvSources().load(str(path), remember=True)
    os.environ.pop(ENV_BRAVE_API_KEY)


def test_a_remembered_file_from_another_directory_leads_the_report_with_a_warning(isolated_home: Path) -> None:
    elsewhere = write(isolated_home / "other-project" / ".env", f"{ENV_BRAVE_API_KEY}={KEY}\n")
    _remember(elsewhere)

    sources = EnvSources.at_startup()

    assert sources.foreign() == [elsewhere]
    report = sources.report()
    assert next(iter(report)) == "warning", "the warning comes before anything else in the env report"
    assert str(elsewhere) in report["warning"]
    assert str(user_config_dir() / MCP_ENV_POINTER_FILE) in report["warning"], "it says how to stop it"
    assert KEY not in str(report), "never a value"
    assert str(elsewhere) in sources.noted("Instructions.")


def test_a_remembered_file_inside_the_working_directory_is_not_foreign(isolated_home: Path) -> None:
    here = write(Path.cwd() / "config" / "web.env", f"{ENV_BRAVE_API_KEY}={KEY}\n")
    _remember(here)

    sources = EnvSources.at_startup()

    assert sources.foreign() == []
    assert "warning" not in sources.report()
    assert sources.noted("Instructions.") == "Instructions."


def test_with_nothing_remembered_there_is_no_warning(isolated_home: Path) -> None:
    sources = EnvSources.at_startup()

    assert sources.foreign() == []
    assert "warning" not in sources.report()


def test_a_link_inside_the_project_to_a_file_elsewhere_is_still_foreign(isolated_home: Path) -> None:
    elsewhere = write(isolated_home / "other-project" / ".env", f"{ENV_BRAVE_API_KEY}={KEY}\n")
    link = Path.cwd() / "linked.env"
    link.symlink_to(elsewhere)
    _remember(link)

    assert EnvSources.at_startup().foreign() == [link]
