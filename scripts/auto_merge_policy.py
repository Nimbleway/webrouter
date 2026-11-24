from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

QODO_LABEL = "Qodo approved"
DecisionName = Literal["ignore", "reset", "hold", "ready"]


@dataclass(frozen=True)
class Decision:
    name: DecisionName
    head: str
    reason: str


def decide(action: str, event_head: str, event_label: str | None, pull_request: dict[str, object]) -> Decision:
    head = _nested_string(pull_request, "head", "sha")
    if event_head != head:
        return Decision("ignore", head, "event head is stale")
    if pull_request.get("state") != "open":
        return Decision("ignore", head, "pull request is not open")
    if _nested_string(pull_request, "base", "ref") != "main":
        return Decision("ignore", head, "base is not main")

    if action in {"opened", "reopened", "synchronize"}:
        return Decision("reset", head, "new pull request state")
    if action in {"labeled", "unlabeled"} and event_label != QODO_LABEL:
        return Decision("ignore", head, "unrelated label event")
    if pull_request.get("draft") is True:
        return Decision("hold", head, "pull request is draft")
    if action == "unlabeled":
        if QODO_LABEL in _label_names(pull_request):
            return Decision("ignore", head, "Qodo approval is present again")
        return Decision("hold", head, "Qodo approval was removed")

    if action in {"labeled", "ready_for_review"} and QODO_LABEL in _label_names(pull_request):
        return Decision("ready", head, "Qodo approved the current head")
    return Decision("hold", head, "current head is not Qodo approved")


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
    parser.add_argument("--event-head", required=True)
    parser.add_argument("--event-label")
    parser.add_argument("--pull-request", required=True, type=Path)
    parser.add_argument("--github-output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    with args.pull_request.open() as source:
        pull_request = json.load(source)
    if not isinstance(pull_request, dict):
        raise ValueError("pull request payload must be a JSON object")

    decision = decide(args.action, args.event_head, args.event_label, pull_request)
    result = f"decision={decision.name}\nhead={decision.head}\nreason={decision.reason}\n"
    if args.github_output:
        with args.github_output.open("a") as output:
            output.write(result)
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
