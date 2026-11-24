from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

QODO_LABEL = "Qodo approved"
QODO_ACTOR_ID = "151058649"
DecisionName = Literal["ignore", "reset", "hold", "cleanup", "approve", "ready"]


@dataclass(frozen=True)
class Decision:
    name: DecisionName
    head: str
    reason: str


def decide(
    action: str,
    event_head: str,
    event_label: str | None,
    pull_request: dict[str, object],
    *,
    event_actor_id: str | None = None,
    previous_base: str | None = None,
    qodo_status: str | None = None,
) -> Decision:
    head = _nested_string(pull_request, "head", "sha")
    guard = _guard_event(action, event_head, head, pull_request, previous_base)
    if guard:
        return guard

    if action in {"opened", "reopened", "synchronize"}:
        return Decision("reset", head, "new pull request state")
    if action == "edited":
        return _edited_decision(head, previous_base)
    if action in {"labeled", "unlabeled"} and event_label != QODO_LABEL:
        return Decision("ignore", head, "unrelated label event")
    if action == "unlabeled":
        return _unlabeled_decision(head, pull_request)
    if action == "labeled":
        return _labeled_decision(head, pull_request, event_actor_id)
    if pull_request.get("draft") is True:
        return Decision("hold", head, "pull request is draft")
    if action == "ready_for_review":
        return _ready_for_review_decision(head, pull_request, event_actor_id, qodo_status)
    return Decision("hold", head, "current head is not Qodo approved")


def _guard_event(
    action: str,
    event_head: str,
    head: str,
    pull_request: dict[str, object],
    previous_base: str | None,
) -> Decision | None:
    if event_head != head:
        return Decision("ignore", head, "event head is stale")
    if pull_request.get("state") != "open":
        return Decision("ignore", head, "pull request is not open")
    if _nested_string(pull_request, "base", "ref") != "main":
        if action == "edited" and previous_base == "main":
            return Decision("cleanup", head, "base changed away from main")
        return Decision("ignore", head, "base is not main")
    return None


def _edited_decision(head: str, previous_base: str | None) -> Decision:
    if previous_base:
        return Decision("reset", head, "base changed to main")
    return Decision("ignore", head, "pull request metadata changed")


def _unlabeled_decision(head: str, pull_request: dict[str, object]) -> Decision:
    if QODO_LABEL in _label_names(pull_request):
        return Decision("ignore", head, "Qodo approval is present again")
    return Decision("hold", head, "Qodo approval was removed")


def _labeled_decision(head: str, pull_request: dict[str, object], event_actor_id: str | None) -> Decision:
    if event_actor_id != QODO_ACTOR_ID:
        return Decision("hold", head, "Qodo label was not applied by Qodo")
    if QODO_LABEL not in _label_names(pull_request):
        return Decision("ignore", head, "Qodo approval is no longer present")
    if pull_request.get("draft") is True:
        return Decision("approve", head, "Qodo approved the current draft head")
    return Decision("ready", head, "Qodo approved the current head")


def _ready_for_review_decision(
    head: str,
    pull_request: dict[str, object],
    event_actor_id: str | None,
    qodo_status: str | None,
) -> Decision:
    if QODO_LABEL not in _label_names(pull_request) or qodo_status != "success":
        return Decision("hold", head, "current head is not Qodo approved")
    if event_actor_id != QODO_ACTOR_ID:
        return Decision("hold", head, "Qodo label was not applied by Qodo")
    return Decision("ready", head, "Qodo approved the current head")


def _nested_string(data: dict[str, object], parent: str, child: str) -> str:
    nested = data.get(parent)
    if not isinstance(nested, dict) or not isinstance(nested.get(child), str):
        raise ValueError(f"pull request is missing {parent}.{child}")
    return nested[child]


def _label_names(pull_request: dict[str, object]) -> set[str]:
    labels: set[str] = set()
    raw_labels = pull_request.get("labels", [])
    if not isinstance(raw_labels, list):
        return labels
    for label in raw_labels:
        if isinstance(label, dict) and isinstance(label.get("name"), str):
            labels.add(label["name"])
    return labels


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Decide how the auto-merge controller handles a pull request event.")
    parser.add_argument("--action", required=True)
    parser.add_argument("--event-actor-id")
    parser.add_argument("--event-head", required=True)
    parser.add_argument("--event-label")
    parser.add_argument("--previous-base")
    parser.add_argument("--pull-request", required=True, type=Path)
    parser.add_argument("--qodo-status")
    parser.add_argument("--github-output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    with args.pull_request.open() as source:
        pull_request = json.load(source)
    if not isinstance(pull_request, dict):
        raise ValueError("pull request payload must be a JSON object")

    decision = decide(
        args.action,
        args.event_head,
        args.event_label,
        pull_request,
        event_actor_id=args.event_actor_id,
        previous_base=args.previous_base,
        qodo_status=args.qodo_status,
    )
    result = f"decision={decision.name}\nhead={decision.head}\nreason={decision.reason}\n"
    with args.github_output.open("a") as output:
        output.write(result)


if __name__ == "__main__":
    main()
