from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

QODO_LABEL = "Qodo approved"
QODO_ACTOR_ID = "151058649"
DecisionName = Literal["ignore", "disable", "ready"]


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
    label_actor_id: str | None = None,
    previous_base: str | None = None,
) -> Decision:
    head = _nested_string(pull_request, "head", "sha")
    guard = _guard_event(action, event_head, head, pull_request, previous_base)
    if guard:
        return guard

    if action in {"opened", "reopened", "synchronize"}:
        return Decision("disable", head, "new pull request state")
    if action == "edited" and previous_base:
        return Decision("disable", head, "base changed to main")
    return _current_state_decision(head, pull_request, label_actor_id)


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
            return Decision("disable", head, "base changed away from main")
        return Decision("ignore", head, "base is not main")
    return None


def _current_state_decision(
    head: str,
    pull_request: dict[str, object],
    label_actor_id: str | None,
) -> Decision:
    if pull_request.get("draft") is True:
        return Decision("disable", head, "pull request is a draft")
    if QODO_LABEL not in _label_names(pull_request):
        return Decision("disable", head, "current head is not Qodo approved")
    if label_actor_id != QODO_ACTOR_ID:
        return Decision("disable", head, "Qodo label was not applied by Qodo")
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
    parser.add_argument("--label-actor-id")
    parser.add_argument("--event-head", required=True)
    parser.add_argument("--event-label")
    parser.add_argument("--previous-base")
    parser.add_argument("--pull-request", required=True, type=Path)
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
        label_actor_id=args.label_actor_id,
        previous_base=args.previous_base,
    )
    result = f"decision={decision.name}\nhead={decision.head}\nreason={decision.reason}\n"
    with args.github_output.open("a") as output:
        output.write(result)


if __name__ == "__main__":
    main()
