"""The Layer 1 spec leads these invariants.

The normative block in docs/spec/layer-1-contract.md is checked against the
registries and constants. Fail-loud on the public methods stays in
tests/test_contract.py and the other contract modules. No network.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import web_router
from web_router.constants import (
    AUTO_APPENDED,
    AUTO_BROWSE_PREFERRED,
    AUTO_BROWSER_KEYLESS,
    AUTO_BROWSER_PREFERRED,
    AUTO_EXTRACT_APPENDED,
    AUTO_EXTRACT_KEYLESS,
    AUTO_EXTRACT_PREFERRED,
    AUTO_KEYLESS,
    AUTO_PREFERRED,
    AUTO_RESEARCH_PREFERRED,
    EXTRA_ALL,
    EXTRA_ANSWER,
    EXTRA_AWS,
    EXTRA_BROWSER,
    EXTRA_EVALS,
    EXTRA_EXTRACT,
    EXTRA_LANGCHAIN,
    EXTRA_MCP,
    EXTRA_RESEARCH,
    EXTRA_SEARCH,
)
from web_router.registry import (
    _BUILTIN_ANSWER_PROVIDERS,
    _BUILTIN_BROWSE_PROVIDERS,
    _BUILTIN_BROWSER_PROVIDERS,
    _BUILTIN_EXTRACT_PROVIDERS,
    _BUILTIN_PROVIDERS,
    _BUILTIN_RESEARCH_PROVIDERS,
    _BUILTIN_TEMPLATE_PROVIDERS,
)

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = _ROOT / "docs" / "spec" / "layer-1-contract.md"
_BLOCK = re.compile(r"```text\n# layer1-chains\n(.*?)```", re.DOTALL)
_EXTRA_KEY = re.compile(r"(?m)^([A-Za-z0-9_-]+) =")
_NEXT_EXTRA = re.compile(r"(?m)^[A-Za-z0-9_-]+\s*=")
_ALL_MEMBER = re.compile(r"web-router\[([^\]]*)\]")

# What `all` installs today. A further member inside the brackets still passes.
_ALL_INCLUDES = frozenset({"search", "extract", "research", "answer", "browser", "aws"})

_CODE_EXTRAS = {
    EXTRA_SEARCH,
    EXTRA_EXTRACT,
    EXTRA_RESEARCH,
    EXTRA_ANSWER,
    EXTRA_BROWSER,
    EXTRA_MCP,
    EXTRA_EVALS,
    EXTRA_LANGCHAIN,
    EXTRA_AWS,
    EXTRA_ALL,
}


def _block() -> dict[str, tuple[str, ...]]:
    text = _SPEC.read_text(encoding="utf-8")
    found = _BLOCK.search(text)
    assert found is not None, "layer1-chains block missing from the Layer 1 spec"
    parsed: dict[str, tuple[str, ...]] = {}
    for line in found.group(1).splitlines():
        if not line.strip():
            continue
        key, sep, raw = line.partition(":")
        assert sep, line
        parsed[key.strip()] = tuple(raw.split())
    return parsed


def _names(entries: tuple[tuple[str, dict[str, Any]], ...]) -> tuple[str, ...]:
    return tuple(name for name, _ in entries)


def _pyproject_section() -> str:
    text = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return text.split("[project.optional-dependencies]", 1)[1].split("\n[", 1)[0]


def _pyproject_extras() -> set[str]:
    return set(_EXTRA_KEY.findall(_pyproject_section()))


def _pyproject_all_members() -> set[str]:
    """Members named inside the `all` extra, however the list is wrapped."""
    section = _pyproject_section()
    marker = re.search(r"(?m)^all\s*=", section)
    assert marker is not None, "all extra missing from pyproject.toml"
    rest = section[marker.end() :]
    next_key = _NEXT_EXTRA.search(rest)
    body = rest[: next_key.start()] if next_key else rest
    members: set[str] = set()
    for match in _ALL_MEMBER.finditer(body):
        members.update(part.strip() for part in match.group(1).split(",") if part.strip())
    assert members, "all extra names no members"
    return members


def test_named_backends_match_the_registries() -> None:
    """A backend the code ships is in the spec, including agentcore, and the reverse."""
    chains = _block()
    assert set(chains["search.backends"]) == set(_BUILTIN_PROVIDERS)
    assert "agentcore" in chains["search.backends"]
    assert set(chains["extract.backends"]) == set(_BUILTIN_EXTRACT_PROVIDERS)
    assert set(chains["template.backends"]) == set(_BUILTIN_TEMPLATE_PROVIDERS)
    assert set(chains["research.backends"]) == set(_BUILTIN_RESEARCH_PROVIDERS)
    assert set(chains["answer.backends"]) == set(_BUILTIN_ANSWER_PROVIDERS)
    assert set(chains["browser.backends"]) == set(_BUILTIN_BROWSER_PROVIDERS)
    assert set(chains["browse.backends"]) == set(_BUILTIN_BROWSE_PROVIDERS)


def test_auto_chain_order_matches_constants() -> None:
    """The spec's tiers are the constants, in that order. agentcore is last in search.appended."""
    chains = _block()
    assert chains["search.preferred"] == AUTO_PREFERRED
    assert chains["search.keyless"] == _names(AUTO_KEYLESS)
    assert chains["search.appended"] == AUTO_APPENDED
    assert chains["search.appended"][-1] == "agentcore"
    assert chains["extract.preferred"] == AUTO_EXTRACT_PREFERRED
    assert chains["extract.keyless"] == _names(AUTO_EXTRACT_KEYLESS)
    assert chains["extract.appended"] == AUTO_EXTRACT_APPENDED
    assert chains["research.preferred"] == AUTO_RESEARCH_PREFERRED
    assert chains["browser.preferred"] == AUTO_BROWSER_PREFERRED
    assert chains["browser.keyless"] == _names(AUTO_BROWSER_KEYLESS)
    assert chains["browse.preferred"] == AUTO_BROWSE_PREFERRED

    for method in ("search", "extract", "research", "answer", "browser", "browse"):
        registered = set(chains[f"{method}.backends"])
        for tier in ("preferred", "keyless", "appended"):
            key = f"{method}.{tier}"
            if key not in chains:
                continue
            missing = set(chains[key]) - registered
            assert not missing, (key, missing)


def test_extras_include_aws_for_agentcore() -> None:
    """Method extras are not the whole set: aws installs agentcore, and all includes it."""
    chains = _block()
    spec_extras = set(chains["extras"])
    assert spec_extras == _CODE_EXTRAS
    assert spec_extras == _pyproject_extras()
    assert "aws" in spec_extras
    assert _BUILTIN_PROVIDERS["agentcore"][2] == EXTRA_AWS == "aws"
    missing = _ALL_INCLUDES - _pyproject_all_members()
    assert not missing, missing


def test_package_version_matches_the_library() -> None:
    """The block's package line equals web_router.__version__. A release bump updates both."""
    chains = _block()
    assert chains["package"] == (web_router.__version__,)
