from __future__ import annotations

import json

import pytest

from scripts import auto_merge_policy as policy

HEAD = "a" * 40
REPO = "Nimbleway/webrouter"
EVENT = {"event": "labeled", "actor": {"id": policy.QODO_ACTOR_ID}, "created_at": "2026-11-27T12:01:00Z"}
STATUS = {
    "context": "qodo-reviewed",
    "creator": {"login": "controller[bot]"},
    "state": "success",
    "description": "Qodo reviewed; started=2026-11-27T12:00:00Z",
}


def pull_request(**changes: object) -> dict:
    return {
        "state": "open",
        "draft": False,
        "head": {"sha": HEAD},
        "base": {"ref": "main"},
        "labels": [{"name": policy.QODO_LABEL}],
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [{"labels": []}, {"draft": True}, {"base": {"ref": "integration"}}],
)
def test_ineligible_pull_requests_disarm(changes: dict) -> None:
    assert policy.decide(pull_request(**changes), EVENT, STATUS) == "disable"


@pytest.mark.parametrize(
    "event,status",
    [
        (dict(EVENT, actor={"id": 123}), STATUS),
        (dict(EVENT, event="unlabeled"), STATUS),
        (dict(EVENT, created_at="2026-11-27T11:59:00Z"), STATUS),
        (dict(EVENT, created_at="2026-11-27T12:01:00"), STATUS),
        (EVENT, {}),
        (EVENT, dict(STATUS, description="Qodo reviewed; started=invalid")),
    ],
)
def test_untrusted_or_stale_approval_disarms(event: dict, status: dict) -> None:
    assert policy.decide(pull_request(), event, status) == "disable"


@pytest.mark.parametrize("label_removed", [False, True])
def test_reconcile_checks_live_evidence_twice_before_acting(
    monkeypatch: pytest.MonkeyPatch, label_removed: bool
) -> None:
    pulls = iter([pull_request(), pull_request(labels=[]) if label_removed else pull_request()])
    commands = []

    def gh(*args: str) -> str:
        if args[-1].endswith("/pulls/123"):
            return json.dumps(next(pulls))
        if "/events?" in args[-1]:
            event = dict(EVENT, label={"name": policy.QODO_LABEL})
            return json.dumps([[], [event]])
        if "/status?" in args[-1]:
            other = dict(STATUS, creator={"login": "other[bot]"}, state="pending")
            return json.dumps([{"statuses": [other]}, {"statuses": [STATUS]}])
        if args[:2] == ("pr", "view"):
            return json.dumps({"autoMergeRequest": {"mergeMethod": "SQUASH"} if label_removed else None})
        commands.append(args)
        return ""

    monkeypatch.setattr(policy.review, "gh", gh)
    policy.reconcile(REPO, 123, "controller")

    action = ("--disable-auto",) if label_removed else ("--auto", "--squash", "--match-head-commit", HEAD)
    assert commands == [("pr", "merge", "123", "--repo", REPO, *action)]
