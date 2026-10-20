"""The decider slot: the locked decision schema, the registry, the three built-in deciders and the validating
wrapper, with no network."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import types
import typing
from pathlib import Path
from typing import Any

import httpx
import pytest

from web_router import WebRouter
from web_router.constants import (
    DECISION_ERROR_EXCEPTION,
    DECISION_ERROR_HTTP,
    DECISION_ERROR_INVALID_ANSWER,
    DECISION_ERROR_TIMEOUT,
    DECISION_SCHEMA_VERSION,
    DECISION_STATUS_ANSWERED,
    DECISION_STATUS_FAILED,
    DECISION_STATUS_NOT_ASKED,
    ROUTE_FALLBACK_DECIDER_ERROR,
    ROUTE_FALLBACK_LOW_CONFIDENCE,
    ROUTE_FALLBACK_NO_BACKEND,
    ROUTE_FALLBACK_NO_URL,
    ROUTE_METHODS,
    ROUTE_QUESTIONS,
)
from web_router.decisions import (
    Answer,
    Decision,
    DecisionProvider,
    JevDecider,
    OpenAIDecider,
    RulesDecider,
    ValidatedDecider,
    available_deciders,
    conformance_violations,
    create_decider,
    register_decider,
    unregister_decider,
    validate_answers,
    validate_decision,
)
from web_router.decisions.openai import prompt_digest
from web_router.decisions.schema import (
    SCHEMA_VERSION,
    DeciderStatus,
    ErrorKind,
    FallbackReason,
    RouteMethod,
)
from web_router.exceptions import ConfigurationError, ProviderError, ProviderNotInstalledError

SNAPSHOT = Path(__file__).parent / "fixtures" / "decisions" / "decision.schema.json"
SURE = 0.9


def label(choice: str, confidence: float = SURE, probabilities: dict[str, float] | None = None) -> dict[str, Any]:
    return {"choice": choice, "confidence": confidence, "probabilities": probabilities or {}}


def valid_labels() -> dict[str, Any]:
    return {
        "tool": label("search", 0.9, {"search": 0.9, "extract": 0.05, "research": 0.05}),
        "recent": label("any", 0.8),
        "coverage": label("lookup", 0.7),
        "well_scoped": label("clear", 0.95),
    }


def valid_decision(**changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "decider_status": "answered",
        "labels": valid_labels(),
        "route": {"method": "search", "tool": "search", "providers": ["tavily", "jina"], "fallback": [], "urls": []},
        "confidence": 0.7,
        "rationale": "Recommended tool: search",
        "decider_id": "jev",
        "decider_version": "jev-1.14.0",
        "latency_ms": 12.5,
    }
    record.update(changes)
    return record


def sure_answers(**choices: str) -> dict[str, Answer]:
    base = {"tool": "search", "recent": "any", "coverage": "lookup", "well_scoped": "clear", **choices}
    return {name: Answer(choice=choice, confidence=SURE) for name, choice in base.items()}


class FakeDecider:
    """Answers with what it was given, or raises what it was given; notes whether it was closed."""

    name = "fake"
    version = "fake-1"

    def __init__(self, answers: Any = None, error: BaseException | None = None) -> None:
        self.answers = answers if answers is not None else sure_answers()
        self.error = error
        self.closed = False

    def decide(self, state: Any, questions: dict[str, Any]) -> Any:
        if self.error is not None:
            raise self.error
        return self.answers

    def close(self) -> None:
        self.closed = True


def router(decider: Any) -> WebRouter:
    return WebRouter(provider_config={"auto": {"chain": {"search": ["tavily"]}}}, decider=decider)


def test_a_valid_decision_is_accepted() -> None:
    decision, found = validate_decision(valid_decision(), "jev-1.14.0")
    assert found == []
    assert decision is not None and decision.schema_version == SCHEMA_VERSION


def test_the_schema_version_constant_and_the_model_agree() -> None:
    assert Decision.model_fields["schema_version"].default == DECISION_SCHEMA_VERSION == SCHEMA_VERSION


def test_the_label_choices_are_exactly_the_route_questions_choices() -> None:
    schema = Decision.model_json_schema()
    for question, spec in ROUTE_QUESTIONS.items():
        options = schema["$defs"]["DecisionLabels"]["properties"][question]["anyOf"]
        ref = next(o["$ref"] for o in options if "$ref" in o).rsplit("/", 1)[-1]
        assert schema["$defs"][ref]["properties"]["choice"]["enum"] == list(spec["criteria"])


def test_the_methods_and_fallback_codes_are_exactly_pr11s() -> None:
    assert typing.get_args(RouteMethod) == ROUTE_METHODS
    codes = {
        ROUTE_FALLBACK_DECIDER_ERROR,
        ROUTE_FALLBACK_LOW_CONFIDENCE,
        ROUTE_FALLBACK_NO_URL,
        ROUTE_FALLBACK_NO_BACKEND,
    }
    assert set(typing.get_args(FallbackReason)) == codes


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"tool": label("scrape")}, "labels.tool.choice"),
        ({"recent": label("year")}, "labels.recent.choice"),
        ({"tool": label("search", 1.2)}, "labels.tool.confidence"),
        ({"tool": label("search", -0.1)}, "labels.tool.confidence"),
        ({"tool": label("search", 0.9, {"search": 0.6, "extract": 0.6})}, "labels.tool"),
        ({"tool": label("search", 0.9, {"search": 1.5})}, "labels.tool"),
        ({"tool": label("search", 0.9, {"extract": 1.0})}, "labels.tool"),
        ({"tool": {**label("search"), "why": "x"}}, "labels.tool.why"),
    ],
)
def test_a_bad_label_is_rejected_and_says_where(change: dict[str, Any], field: str) -> None:
    decision, found = validate_decision(valid_decision(labels={**valid_labels(), **change}), "d")
    assert decision is None
    assert field in [v.field for v in found]


def test_a_missing_label_is_rejected() -> None:
    labels = valid_labels()
    del labels["well_scoped"]
    _, found = validate_decision(valid_decision(labels=labels), "d")
    assert [(v.field, v.type) for v in found] == [("labels.well_scoped", "missing")]


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"route": {"method": "crawl", "tool": "search", "providers": ["tavily"]}}, "route.method"),
        ({"route": {"method": "search", "tool": "scrape", "providers": ["tavily"]}}, "route.tool"),
        ({"route": {"method": "search", "providers": ["tavily"]}}, "route.tool"),
        ({"route": {"method": "search", "tool": "search", "providers": []}}, "route.providers"),
        (
            {"route": {"method": "search", "tool": "search", "providers": ["tavily"], "fallback": ["timeout"]}},
            "route.fallback.0",
        ),
        (
            {"route": {"method": "search", "tool": "search", "providers": ["tavily"], "mapped_config": "nimble+x"}},
            "route.mapped_config",
        ),
        ({"rationale": ""}, "rationale"),
        ({"decider_id": ""}, "decider_id"),
        ({"latency_ms": -1.0}, "latency_ms"),
        ({"schema_version": "decision-v1"}, "schema_version"),
        ({"timeout_s": 0}, "timeout_s"),
        ({"decider_status": "maybe"}, "decider_status"),
        ({"policy": "v5final"}, "policy"),
        ({"decider_label": "stand-in"}, "decider_label"),
    ],
)
def test_a_bad_decision_field_is_rejected_and_says_where(changes: dict[str, Any], field: str) -> None:
    _, found = validate_decision(valid_decision(**changes), "d")
    assert field in [v.field for v in found]


def test_confidence_must_be_the_least_sure_label() -> None:
    _, found = validate_decision(valid_decision(confidence=0.9), "d")
    assert found and "minimum" in found[0].msg


def test_no_labels_means_no_confidence_and_not_a_made_up_one() -> None:
    assert validate_decision(valid_decision(decider_status="not_asked", labels=None, confidence=None), "d")[1] == []
    assert validate_decision(valid_decision(decider_status="not_asked", labels=None, confidence=0.0), "d")[1]


def test_labels_are_set_exactly_when_the_decider_answered() -> None:
    assert validate_decision(valid_decision(decider_status="not_asked"), "d")[1]
    assert validate_decision(valid_decision(labels=None, confidence=None), "d")[1]


def test_a_failed_decider_needs_the_decider_error_fallback_and_only_it_carries_an_error() -> None:
    failed = valid_decision(decider_status="failed", labels=None, confidence=None)
    assert validate_decision(failed, "d")[1]
    failed["route"] = {**failed["route"], "fallback": ["decider_error"]}
    assert validate_decision({**failed, "error": {"kind": "timeout", "message": "read timed out"}}, "d")[1] == []
    assert validate_decision({**failed, "error": {"kind": "gremlins", "message": "x"}}, "d")[1]
    assert validate_decision(valid_decision(error={"kind": "timeout", "message": "x"}), "d")[1]


def test_confidence_leaves_out_well_scoped_which_moves_no_route() -> None:
    labels = {**valid_labels(), "well_scoped": label("missing_context", 0.1)}
    assert validate_decision(valid_decision(labels=labels, confidence=0.7), "d")[1] == []
    assert validate_decision(valid_decision(labels=labels, confidence=0.1), "d")[1]


def test_a_violation_names_the_field_value_decider_message_and_type() -> None:
    _, found = validate_answers({**sure_answers(), "tool": Answer(choice="scrape", confidence=SURE)}, "acme-2")
    assert [v.to_dict() for v in found] == [
        {
            "field": "tool.choice",
            "value": "'scrape'",
            "decider": "acme-2",
            "msg": found[0].msg,
            "type": "value_error",
        }
    ]
    assert "scrape" in found[0].msg


def test_an_answer_that_is_not_a_mapping_is_a_violation_not_a_crash() -> None:
    labels, found = validate_answers([("tool", "search")], "d")
    assert labels is None
    assert [v.type for v in found] == ["not_a_mapping"]


def test_the_decision_schema_is_locked() -> None:
    """A change to the schema must ship a new snapshot and a new SCHEMA_VERSION, deliberately."""
    current = Decision.model_json_schema()
    assert json.loads(SNAPSHOT.read_text()) == current, (
        "The decision schema drifted from tests/fixtures/decisions/decision.schema.json. "
        "If the change is deliberate, bump DECISION_SCHEMA_VERSION and regenerate the snapshot."
    )


def test_the_builtin_deciders_resolve_by_name() -> None:
    assert {"jev", "openai", "rules"} <= set(available_deciders())
    assert isinstance(create_decider("rules"), RulesDecider)


def test_an_unknown_decider_name_is_refused_and_the_known_ones_listed() -> None:
    with pytest.raises(ConfigurationError, match="'rules'"):
        WebRouter(decider="oracle")


def test_a_registered_decider_works_by_name_and_is_closed_by_the_router() -> None:
    built: list[FakeDecider] = []

    def factory() -> FakeDecider:
        built.append(FakeDecider())
        return built[-1]

    register_decider("acme", factory)
    try:
        assert "acme" in available_deciders()
        acme = router("acme")
        assert acme.decider is built[0]
        acme.close()
        assert built[0].closed
    finally:
        unregister_decider("acme")
    assert "acme" not in available_deciders()


def test_a_registered_name_overrides_a_builtin_of_the_same_name() -> None:
    register_decider("rules", FakeDecider)
    try:
        assert isinstance(create_decider("rules"), FakeDecider)
    finally:
        unregister_decider("rules")
    assert isinstance(create_decider("rules"), RulesDecider)


def test_jev_by_name_is_still_built_owned_and_keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="TYPESAFE_API_KEY"):
        WebRouter(decider="jev")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    jev = WebRouter(decider="jev")
    assert isinstance(jev.decider, JevDecider)
    jev.close()
    assert jev.decider._client.is_closed


def test_an_instance_is_used_as_given_and_left_open() -> None:
    decider = FakeDecider()
    wr = router(decider)
    assert wr.decider is decider
    wr.close()
    assert not decider.closed


def test_a_missing_dependency_is_reported_as_an_extra_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "web_router.decisions.jev", raising=False)
    monkeypatch.setitem(sys.modules, "httpx", None)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    with pytest.raises(ProviderNotInstalledError, match="search"):
        create_decider("jev")


def jev_with_reply(payload: Any, monkeypatch: pytest.MonkeyPatch) -> JevDecider:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    jev = JevDecider()
    jev._client = httpx.Client(
        base_url="https://jev.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
    return jev


def test_jev_answers_map_to_a_valid_locked_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "answers": {
            "tool": {"choice": "search", "confidence": 0.93, "probabilities": {"search": 0.93, "research": 0.07}},
            "recent": {"choice": "month", "confidence": 0.81, "probabilities": {"month": 0.81, "any": 0.19}},
            "coverage": {"choice": "lookup", "confidence": 0.74},
            "well_scoped": {"choice": "clear", "confidence": 0.99},
        }
    }
    jev = ValidatedDecider(jev_with_reply(payload, monkeypatch))
    route = router(jev).classify("latest Databricks funding round")
    decision = jev.decision(route)
    assert jev.last_violations == []
    assert decision.decider_id == "jev" and decision.decider_version == "jev-1.14.0"
    assert decision.labels is not None and decision.labels.recent is not None
    assert decision.labels.recent.choice == "month"
    assert decision.confidence == 0.74
    assert decision.route.providers == ["tavily"] and decision.route.fallback == []
    assert (decision.decider_status, decision.route.tool, decision.timeout_s) == ("answered", "search", 0.8)
    assert decision.error is None and decision.violations == []


def test_a_jev_answer_outside_the_schema_falls_back_and_keeps_the_violation(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"answers": {name: {"choice": "maybe", "confidence": 0.9} for name in ROUTE_QUESTIONS}}
    jev = ValidatedDecider(jev_with_reply(payload, monkeypatch))
    route = router(jev).classify("who won")
    assert route.fallback == (ROUTE_FALLBACK_DECIDER_ERROR,)
    assert len(jev.last_violations) == 4 and jev.violations == jev.last_violations
    decision = jev.decision(route)
    assert decision.labels is None and decision.confidence is None
    assert decision.decider_status == "failed"
    assert decision.error is not None and decision.error.kind == "invalid_answer"
    assert decision.violations == jev.last_violations


def test_an_invalid_answer_falls_back_to_the_default_route_and_says_why() -> None:
    decider = ValidatedDecider(FakeDecider(sure_answers(tool="scrape")))
    route = router(decider).classify("who won")
    assert (route.method, route.fallback) == ("search", (ROUTE_FALLBACK_DECIDER_ERROR,))
    assert [v.field for v in decider.last_violations] == ["tool.choice"]
    assert decider.last_error is not None and "tool.choice" in str(decider.last_error)


def test_any_exception_from_a_provider_falls_back_rather_than_crashing_classify() -> None:
    decider = ValidatedDecider(FakeDecider(error=RuntimeError("vendor bug")))
    route = router(decider).classify("who won")
    assert route.fallback == (ROUTE_FALLBACK_DECIDER_ERROR,)
    assert decider.last_error is not None and isinstance(decider.last_error.original, RuntimeError)
    assert decider.last_violations == []


def test_a_provider_error_passes_through_as_the_decider_error_fallback() -> None:
    decider = ValidatedDecider(FakeDecider(error=ProviderError("fake", "down")))
    assert router(decider).classify("who won").fallback == (ROUTE_FALLBACK_DECIDER_ERROR,)


def test_a_valid_but_unsure_answer_takes_the_low_confidence_fallback() -> None:
    unsure = {**sure_answers(tool="research"), "tool": Answer(choice="research", confidence=0.3)}
    decider = ValidatedDecider(FakeDecider(unsure))
    route = router(decider).classify("who won")
    assert (route.method, route.fallback) == ("search", (ROUTE_FALLBACK_LOW_CONFIDENCE,))
    assert decider.last_violations == []
    assert decider.decision(route).confidence == 0.3


def test_the_wrapper_closes_what_it_wraps() -> None:
    inner = FakeDecider()
    ValidatedDecider(inner).close()
    assert inner.closed


def test_a_decision_records_pr11s_route_fallbacks() -> None:
    decider = ValidatedDecider(FakeDecider(sure_answers(tool="extract")))
    decision = decider.decision(router(decider).classify("read the pricing page"))
    assert decision.route.method == "search"
    assert decision.route.fallback == [ROUTE_FALLBACK_NO_URL]
    decision = decider.decision(router(decider).classify("x", urls="https://example.com"))
    assert decision.labels is None and decision.route.urls == ["https://example.com"]
    assert (decision.decider_status, decision.route.tool, decision.violations) == ("not_asked", "extract", [])


def test_a_method_with_no_backend_is_recorded_as_such() -> None:
    decider = ValidatedDecider(FakeDecider(sure_answers(tool="browse")))
    wr = WebRouter(provider_config={"auto": {"chain": {"search": ["tavily"], "browse": []}}}, decider=decider)
    assert decider.decision(wr.classify("log in and book")).route.fallback == [ROUTE_FALLBACK_NO_BACKEND]


def fake_openai(content: str) -> tuple[types.ModuleType, dict[str, Any]]:
    """The SDK's stand-in: records the client's options and every request, and answers ``content``."""
    seen: dict[str, Any] = {"built": [], "requests": [], "closed": 0}

    class Completions:
        def create(self, **request: Any) -> Any:
            seen["requests"].append(request)
            message = types.SimpleNamespace(content=content)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            seen["built"].append(kwargs)
            self.chat = types.SimpleNamespace(completions=Completions())

        def close(self) -> None:
            seen["closed"] += 1

    module = types.ModuleType("openai")
    module.OpenAI = Client  # type: ignore[attr-defined]
    return module, seen


def model_reply(**choices: str) -> str:
    reply = {}
    for name, spec in ROUTE_QUESTIONS.items():
        options = list(spec["criteria"])
        pick = choices.get(name, options[0])
        reply[name] = {
            "choice": pick,
            "probabilities": {o: (0.85 if o == pick else 0.15 / (len(options) - 1)) for o in options},
        }
    return json.dumps(reply)


def test_the_openai_decider_asks_for_structured_labels_and_reads_them(monkeypatch: pytest.MonkeyPatch) -> None:
    module, seen = fake_openai(model_reply(recent="week"))
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    decider = ValidatedDecider(OpenAIDecider())
    route = router(decider).classify("this week's OpenAI announcements")
    assert route.labels["recent"].choice == "week" and route.labels["recent"].confidence == 0.85
    assert decider.last_violations == []
    request = seen["requests"][0]
    schema = request["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["properties"]["tool"]["properties"]["choice"]["enum"] == list(
        ROUTE_QUESTIONS["tool"]["criteria"]
    )
    assert "this week's OpenAI announcements" in request["messages"][0]["content"]
    assert seen["built"][0]["api_key"] == "test-key"
    assert re.fullmatch(r"gpt-4\.1-mini\+p[0-9a-f]{8}", decider.decision(route).decider_version)
    assert decider.decision(route).timeout_s == 5.0
    decider.close()
    assert seen["closed"] == 1


def test_the_openai_decider_reads_its_key_from_the_environment_only(monkeypatch: pytest.MonkeyPatch) -> None:
    module, _ = fake_openai(model_reply())
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        WebRouter(decider="openai")


def test_the_openai_decider_without_its_sdk_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "openai", None)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with pytest.raises(ProviderNotInstalledError, match="evals"):
        OpenAIDecider()


def test_a_client_handed_to_the_openai_decider_needs_no_key_and_stays_open(monkeypatch: pytest.MonkeyPatch) -> None:
    module, seen = fake_openai(model_reply())
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    decider = OpenAIDecider("gpt-4.1", client=module.OpenAI())
    decider.decide({"query": "q"}, ROUTE_QUESTIONS)
    decider.close()
    assert seen["requests"][0]["model"] == "gpt-4.1" and seen["closed"] == 0


@pytest.mark.parametrize("content", ["not json", json.dumps({"tool": {"choice": "search"}}), json.dumps([1])])
def test_an_unreadable_openai_reply_is_a_provider_error(content: str, monkeypatch: pytest.MonkeyPatch) -> None:
    module, _ = fake_openai(content)
    decider = OpenAIDecider(client=module.OpenAI())
    with pytest.raises(ProviderError):
        decider.decide({"query": "q"}, ROUTE_QUESTIONS)
    assert router(decider).classify("who won").fallback == (ROUTE_FALLBACK_DECIDER_ERROR,)


def test_an_openai_sdk_failure_is_a_provider_error() -> None:
    def fail(**request: Any) -> Any:
        raise TimeoutError("read timed out")

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=fail)))
    with pytest.raises(ProviderError) as caught:
        OpenAIDecider(client=client).decide({"query": "q"}, ROUTE_QUESTIONS)
    assert isinstance(caught.value.original, TimeoutError)


@pytest.mark.parametrize(
    ("query", "tool", "coverage", "well_scoped"),
    [
        ("Who is the CEO of Databricks?", "search", "lookup", "clear"),
        ("Summarize the page at https://example.com/pricing", "extract", "lookup", "clear"),
        ("Compare the MacBook Air and the Dell XPS 13", "research", "synthesis", "clear"),
        ("opening hours", "search", "lookup", "missing_context"),
    ],
)
def test_the_rules_decider_is_deterministic_and_keyless(query: str, tool: str, coverage: str, well_scoped: str) -> None:
    answers = RulesDecider().decide({"query": query}, ROUTE_QUESTIONS)
    assert answers == RulesDecider().decide({"query": query}, ROUTE_QUESTIONS)
    assert (answers["tool"].choice, answers["coverage"].choice, answers["well_scoped"].choice) == (
        tool,
        coverage,
        well_scoped,
    )


def test_the_rules_decider_routes_a_url_to_extract_by_name() -> None:
    route = router("rules").classify("Summarize https://example.com/pricing")
    assert route.method == "extract" and route.fallback == ()


def test_conformance_reports_a_provider_that_breaks_the_contract() -> None:
    assert [v.type for v in conformance_violations(object())] == ["not_a_provider"]  # type: ignore[arg-type]
    broken = conformance_violations(FakeDecider({"tool": Answer(choice="search", confidence=2.0)}), queries=["q"])
    assert {v.field for v in broken} >= {"tool.confidence", "recent", "coverage", "well_scoped"}
    failing = conformance_violations(FakeDecider(error=ProviderError("fake", "down")), queries=["a", "b"])
    assert [v.type for v in failing] == ["decider_error", "decider_error"]


def conforming_providers(monkeypatch: pytest.MonkeyPatch) -> list[DecisionProvider]:
    module, _ = fake_openai(model_reply())
    jev_reply = {"answers": {name: label(next(iter(spec["criteria"]))) for name, spec in ROUTE_QUESTIONS.items()}}
    return [RulesDecider(), OpenAIDecider(client=module.OpenAI()), jev_with_reply(jev_reply, monkeypatch)]


def test_every_builtin_decider_passes_the_conformance_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same check a vendor runs on its own provider: ``assert conformance_violations(MyDecider()) == []``."""
    for provider in conforming_providers(monkeypatch):
        assert isinstance(provider, DecisionProvider)
        assert conformance_violations(provider) == [], provider.name


def test_a_bare_import_of_the_decider_slot_needs_neither_httpx_nor_openai() -> None:
    code = (
        "import sys; sys.modules['httpx'] = None; sys.modules['openai'] = None\n"
        "from web_router import WebRouter\n"
        "from web_router.decisions import Decision, DecisionProvider, ValidatedDecider, available_deciders\n"
        "assert 'rules' in available_deciders()\n"
        "WebRouter(decider='rules').classify('who won')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_the_status_and_error_kind_types_match_their_constants() -> None:
    assert typing.get_args(DeciderStatus) == (
        DECISION_STATUS_ANSWERED,
        DECISION_STATUS_NOT_ASKED,
        DECISION_STATUS_FAILED,
    )
    kinds = (DECISION_ERROR_TIMEOUT, DECISION_ERROR_HTTP, DECISION_ERROR_INVALID_ANSWER, DECISION_ERROR_EXCEPTION)
    assert typing.get_args(ErrorKind) == kinds


def jev_raising(handler: Any, monkeypatch: pytest.MonkeyPatch) -> JevDecider:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    jev = JevDecider()
    jev._client = httpx.Client(base_url="https://jev.test", transport=httpx.MockTransport(handler))
    return jev


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("read timed out", request=request)


@pytest.mark.parametrize(
    ("handler", "kind"),
    [
        (_timeout, "timeout"),
        (lambda request: httpx.Response(503, text="busy"), "http_error"),
        (lambda request: httpx.Response(200, json={"answers": []}), "invalid_answer"),
        (lambda request: httpx.Response(200, text="not json"), "invalid_answer"),
    ],
)
def test_a_failed_decider_records_what_kind_of_failure_it_was(
    handler: Any, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    jev = ValidatedDecider(jev_raising(handler, monkeypatch))
    route = router(jev).classify("who won")
    assert route.fallback == (ROUTE_FALLBACK_DECIDER_ERROR,)
    decision = jev.decision(route)
    assert decision.decider_status == "failed"
    assert decision.error is not None and decision.error.kind == kind and decision.error.message


def test_an_unexpected_exception_is_recorded_as_one() -> None:
    decider = ValidatedDecider(FakeDecider(error=RuntimeError("vendor bug")))
    decision = decider.decision(router(decider).classify("who won"))
    assert decision.error is not None and decision.error.kind == "exception"
    assert "vendor bug" in decision.error.message


def test_a_non_strict_wrapper_routes_on_defaults_for_bad_labels_and_keeps_the_violations() -> None:
    answers = {**sure_answers(recent="day"), "tool": Answer(choice="scrape", confidence=SURE)}
    decider = ValidatedDecider(FakeDecider(answers), strict=False)
    route = router(decider).classify("who won")
    assert (route.method, route.fallback) == ("search", (ROUTE_FALLBACK_LOW_CONFIDENCE,))
    assert "tool" not in route.labels and route.labels["recent"].choice == "day"
    assert [v.field for v in decider.last_violations] == ["tool.choice"]
    decision = decider.decision(route)
    assert decision.decider_status == "answered" and decision.error is None
    assert decision.labels is not None and decision.labels.tool is None
    assert decision.labels.recent is not None and decision.labels.recent.choice == "day"
    assert [v.field for v in decision.violations] == ["tool.choice"]


def test_a_non_strict_wrapper_given_garbage_routes_on_every_default() -> None:
    decider = ValidatedDecider(FakeDecider(["search"]), strict=False)
    route = router(decider).classify("who won")
    assert route.labels == {} and route.fallback == (ROUTE_FALLBACK_LOW_CONFIDENCE,)
    decision = decider.decision(route)
    assert decision.decider_status == "answered" and decision.confidence is None
    assert [v.type for v in decision.violations] == ["not_a_mapping"]


def test_a_strict_wrapper_is_the_default() -> None:
    assert ValidatedDecider(FakeDecider()).strict is True


class GuardError(PermissionError):
    """What a caller's data policy raises before any call leaves the machine."""


def test_a_passthrough_exception_stops_the_run_instead_of_becoming_a_default_route() -> None:
    decider = ValidatedDecider(FakeDecider(error=GuardError("private data")), passthrough=(PermissionError,))
    with pytest.raises(GuardError):
        router(decider).classify("who won")
    assert decider.last_error is None


def test_without_passthrough_the_same_guard_becomes_the_decider_error_fallback() -> None:
    decider = ValidatedDecider(FakeDecider(error=GuardError("private data")))
    assert router(decider).classify("who won").fallback == (ROUTE_FALLBACK_DECIDER_ERROR,)


def test_the_openai_version_changes_with_the_prompt_or_the_schema() -> None:
    changed = {**ROUTE_QUESTIONS, "tool": {**ROUTE_QUESTIONS["tool"], "instructions": "Pick a tool."}}
    assert re.fullmatch(r"[0-9a-f]{8}", prompt_digest())
    assert prompt_digest(changed) != prompt_digest()
    assert prompt_digest() == prompt_digest(dict(ROUTE_QUESTIONS))


def test_a_decision_record_rounds_confidences_and_latency() -> None:
    answers = {
        **sure_answers(),
        "tool": Answer(choice="search", confidence=0.876543, probabilities={"search": 0.876543, "browse": 0.123457}),
    }
    decider = ValidatedDecider(FakeDecider(answers))
    route = router(decider).classify("who won")
    route.latency_ms = 12.3456
    decision = decider.decision(route)
    assert decision.labels is not None and decision.labels.tool is not None
    assert decision.labels.tool.confidence == 0.8765 and decision.labels.tool.probabilities["browse"] == 0.1235
    assert decision.confidence == 0.8765 and decision.latency_ms == 12.3


def test_an_unsure_well_scoped_label_still_triggers_pr11s_fallback_but_not_the_records_confidence() -> None:
    answers = {**sure_answers(), "well_scoped": Answer(choice="clear", confidence=0.2)}
    decider = ValidatedDecider(FakeDecider(answers))
    route = router(decider).classify("who won")
    assert route.fallback == (ROUTE_FALLBACK_LOW_CONFIDENCE,)
    decision = decider.decision(route)
    assert decision.confidence == SURE
    assert decision.labels is not None and decision.labels.well_scoped is not None
    assert decision.labels.well_scoped.confidence == 0.2


def test_the_raw_tool_label_is_kept_when_a_fallback_moves_the_method() -> None:
    answers = {**sure_answers(), "tool": Answer(choice="browse", confidence=0.3)}
    decider = ValidatedDecider(FakeDecider(answers))
    decision = decider.decision(router(decider).classify("who won"))
    assert (decision.route.method, decision.route.tool) == ("search", "browse")


def test_a_decision_for_another_query_carries_no_stale_error() -> None:
    decider = ValidatedDecider(FakeDecider(error=RuntimeError("down")))
    wr = router(decider)
    first = wr.classify("who won")
    decider.provider = FakeDecider()  # type: ignore[assignment]
    wr.classify("who lost")
    decision = decider.decision(first)
    assert decision.decider_status == "failed" and decision.error is None
