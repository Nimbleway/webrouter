from __future__ import annotations

import argparse
import json
from typing import Any, Literal

from scripts import qodo_reviewed as review

QODO_LABEL = "Qodo approved"
QODO_ACTOR_ID = 151058649
REVIEW_PREFIX = "Qodo reviewed; started="
Decision = Literal["ignore", "disable", "ready"]


def _ineligible(pull: dict[str, Any]) -> Decision | None:
    if pull.get("state") != "open":
        return "ignore"
    if pull["base"]["ref"] != "main" or pull.get("draft") is not False:
        return "disable"
    if not any(label.get("name") == QODO_LABEL for label in pull.get("labels", [])):
        return "disable"
    return None


def decide(pull: dict[str, Any], event: dict[str, Any], status: dict[str, Any]) -> Decision:
    """Evaluate current PR state and authenticated Qodo evidence."""
    if ineligible := _ineligible(pull):
        return ineligible
    label_time = review.parse_utc_time(event.get("created_at"))
    description = status.get("description")
    review_time = (
        review.parse_utc_time(description[len(REVIEW_PREFIX) :])
        if isinstance(description, str) and description.startswith(REVIEW_PREFIX)
        else None
    )
    if (
        event.get("event") == "labeled"
        and (event.get("actor") or {}).get("id") == QODO_ACTOR_ID
        and status.get("state") == "success"
        and label_time is not None
        and review_time is not None
        and label_time >= review_time
    ):
        return "ready"
    return "disable"


def _read_decision(repo: str, pr: str, app_slug: str) -> tuple[Decision, str]:
    pull = review.github(f"repos/{repo}/pulls/{pr}")
    head = pull["head"]["sha"]
    if ineligible := _ineligible(pull):
        return ineligible, head

    event = {}
    for page in review.github(f"repos/{repo}/issues/{pr}/events?per_page=100", "--paginate", "--slurp"):
        for item in page:
            if item["event"] in {"labeled", "unlabeled"} and (item.get("label") or {}).get("name") == QODO_LABEL:
                event = item
    statuses = review.github(f"repos/{repo}/commits/{head}/status?per_page=100", "--paginate", "--slurp")
    status = next(
        (
            item
            for page in statuses
            for item in page["statuses"]
            if item["context"] == review.CONTEXT and (item.get("creator") or {}).get("login") == f"{app_slug}[bot]"
        ),
        {},
    )
    return decide(pull, event, status), head


def reconcile(repo: str, pr: int, app_slug: str) -> None:
    """Reconcile GitHub's auto-merge request with current evidence."""
    number = str(pr)
    decision, head = _read_decision(repo, number, app_slug)
    if decision == "ready":
        decision, head = _read_decision(repo, number, app_slug)
    if decision == "ignore":
        return

    result = review.gh("pr", "view", number, "--repo", repo, "--json", "autoMergeRequest")
    method = json.loads(result)["autoMergeRequest"]
    merge = ("pr", "merge", number, "--repo", repo)
    if method and (decision == "disable" or method["mergeMethod"] != "SQUASH"):
        review.gh(*merge, "--disable-auto")
    if decision == "ready":
        review.gh(*merge, "--auto", "--squash", "--match-head-commit", head)


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile auto-merge for a pull request")
    parser.add_argument("repo")
    parser.add_argument("pr", type=int)
    parser.add_argument("app_slug")
    args = parser.parse_args()
    reconcile(args.repo, args.pr, args.app_slug)


if __name__ == "__main__":
    main()
