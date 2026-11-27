from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
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
    pull_request: dict[str, object],
    *,
    label_actor_id: str | None = None,
    label_applied_at: str | None = None,
    review_started_at: str | None = None,
) -> Decision:
    head = _nested_string(pull_request, "head", "sha")
    guard = _guard_request(head, pull_request)
    if guard:
        return guard

    return _current_state_decision(head, pull_request, label_actor_id, label_applied_at, review_started_at)


def _guard_request(
    head: str,
    pull_request: dict[str, object],
) -> Decision | None:
    if pull_request.get("state") != "open":
        return Decision("ignore", head, "pull request is not open")
    if _nested_string(pull_request, "base", "ref") != "main":
        return Decision("disable", head, "base is not main")
    return None


def _current_state_decision(
    head: str,
    pull_request: dict[str, object],
    label_actor_id: str | None,
    label_applied_at: str | None,
    review_started_at: str | None,
) -> Decision:
    if pull_request.get("draft") is True:
        return Decision("disable", head, "pull request is a draft")
    if QODO_LABEL not in _label_names(pull_request):
        return Decision("disable", head, "current head is not Qodo approved")
    if label_actor_id != QODO_ACTOR_ID:
        return Decision("disable", head, "Qodo label was not applied by Qodo")
    label_time, review_time = _utc_time(label_applied_at), _utc_time(review_started_at)
    if label_time is None or review_time is None or label_time < review_time:
        return Decision("disable", head, "label does not cover the completed review")
    return Decision("ready", head, "Qodo approved the current head")


def _utc_time(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value) if value else None
        return parsed if parsed and parsed.utcoffset() == timedelta(0) else None
    except ValueError:
        return None


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
    parser.add_argument("--label-actor-id")
    parser.add_argument("--label-applied-at")
    parser.add_argument("--review-started-at")
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
        pull_request,
        label_actor_id=args.label_actor_id,
        label_applied_at=args.label_applied_at,
        review_started_at=args.review_started_at,
    )
    result = f"decision={decision.name}\nhead={decision.head}\nreason={decision.reason}\n"
    with args.github_output.open("a") as output:
        output.write(result)


if __name__ == "__main__":
    main()
