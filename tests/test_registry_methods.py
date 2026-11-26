"""The per-method providers table: what runs keyless, and what a method with nothing to run needs."""

from __future__ import annotations

import pytest

from web_router import __version__
from web_router.cli import run
from web_router.registry_methods import KEYED_ENV_BY_METHOD, providers_report


def _no_paid_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for table in KEYED_ENV_BY_METHOD.values():
        for env_vars in table.values():
            for env_var in env_vars:
                monkeypatch.delenv(env_var, raising=False)


def test_a_method_with_nothing_to_run_lists_no_auto_and_the_keys_that_would_add_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_paid_keys(monkeypatch)
    report = providers_report()

    assert report["research"] == [] and report["browse"] == []
    assert "LINKUP_API_KEY" in report["needs_key"]["research"]
    assert "SKYVERN_API_KEY" in report["needs_key"]["browse"]
    assert set(report["needs_key"]) == {"research", "browse", "answer", "live_data"}


def test_a_key_moves_its_method_out_of_needs_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_paid_keys(monkeypatch)
    monkeypatch.setenv("LINKUP_API_KEY", "test-key")
    report = providers_report()

    assert report["research"] == ["auto", "linkup"]
    assert "research" not in report["needs_key"]
    assert "browse" in report["needs_key"]


def test_keyless_lists_what_auto_runs_without_a_key_in_order() -> None:
    keyless = providers_report()["keyless"]

    assert keyless["search"][:2] == ["nimble", "tavily"]
    assert keyless["extract"][:3] == ["nimble", "tavily", "jina"]
    assert keyless["browser"] == ["local"]
    assert "research" not in keyless


def test_version_prints_the_installed_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        run(["--version"])

    assert exited.value.code == 0
    assert capsys.readouterr().out.strip() == f"webrouter {__version__}"
