"""The generated default orders: ``auto`` reads them from ``data/chains.json``, and the README agrees with it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from web_router import WebRouter
from web_router._chains import chains
from web_router.constants import AUTO_APPENDED, AUTO_PREFERRED, ENV_BY_PROVIDER
from web_router.constants_answer import AUTO_ANSWER_PREFERRED

README = Path(__file__).resolve().parents[1] / "README.md"

# Membership before the order was generated, spelled out: a reorder is fine, a member gained or lost is not.
SEARCH_MEMBERS = {
    "perplexity",
    "nimble",
    "linkup",
    "tavily",
    "brave",
    "webiq",
    "parallel",
    "exa",
    "you",
    "keenable",
    "deepseek",
    "agentcore",
}
ANSWER_MEMBERS = {"perplexity", "exa", "you", "linkup", "parallel", "brave"}

README_NAMES = {
    "Perplexity": "perplexity",
    "Nimble": "nimble",
    "Linkup": "linkup",
    "Tavily": "tavily",
    "Brave": "brave",
    "WebIQ": "webiq",
    "Parallel": "parallel",
    "Exa": "exa",
    "You.com": "you",
    "Keenable": "keenable",
    "DeepSeek": "deepseek",
    "AgentCore": "agentcore",
}


def _names(text: str) -> list[str]:
    return [README_NAMES[name.strip()] for name in re.split(r",|\bthen\b", text) if name.strip()]


def test_the_constants_are_the_generated_file() -> None:
    data = chains()
    assert list(AUTO_PREFERRED) == data["search"]["preferred"]
    assert list(AUTO_APPENDED) == data["search"]["appended"]
    assert list(AUTO_ANSWER_PREFERRED) == data["answer"]["preferred"]


def test_the_generated_chains_are_a_permutation_of_todays_members() -> None:
    data = chains()
    search = data["search"]["preferred"] + data["search"]["appended"]
    assert sorted(search) == sorted(SEARCH_MEMBERS)
    assert sorted(data["answer"]["preferred"]) == sorted(ANSWER_MEMBERS)


def test_every_member_is_either_measured_or_marked_unmeasured() -> None:
    for method, parts in (("search", ("preferred", "appended")), ("answer", ("preferred",))):
        entry = chains()[method]
        members = [p for part in parts for p in entry[part]]
        groups = [set(entry["measured"]), set(entry["unmeasured"]), set(entry["no_row_served"])]
        assert sum(len(g) for g in groups) == len(set().union(*groups)), "a member sits in one group only"
        assert set().union(*groups) == set(members)
        measured = list(entry["measured"])
        assert entry["preferred"][: len(measured)] == measured, "the ranked members come first, in their rank"


def test_the_readme_search_table_matches_the_file() -> None:
    row = re.search(r"^\| `search` \| (?P<keyed>[^|]+) \| [^|]+ \| (?P<added>[^|]+) \|$", README.read_text(), re.M)
    assert row is not None
    assert _names(row["keyed"]) == chains()["search"]["preferred"]
    assert _names(row["added"]) == chains()["search"]["appended"]


def test_the_readme_answer_order_matches_the_file() -> None:
    text = " ".join(README.read_text().split())
    found = re.search(r"cheaper at list price: (?P<order>.+?), which the scan has not measured", text)
    assert found is not None
    assert _names(found["order"]) == chains()["answer"]["preferred"]


def test_a_chain_in_provider_config_still_replaces_the_generated_order(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_var in ENV_BY_PROVIDER.values():
        if env_var:
            monkeypatch.setenv(env_var, "test-key")
    mine = ["brave", "nimble"]
    with WebRouter(provider_config={"auto": {"chain": {"search": mine, "answer": ["exa"]}}}) as router:
        assert router.get_provider("auto").members == tuple(mine)  # type: ignore[attr-defined]
        assert router.get_answer_provider("auto").members == ("exa",)  # type: ignore[attr-defined]
