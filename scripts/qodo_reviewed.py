"""Publish review completion without treating open findings as approval."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timedelta
from typing import Any

from scripts.qodo_api import read_review

REPOSITORY = "Nimbleway/webrouter"
CONTEXT = "qodo-reviewed"
ATTEMPTS = 12
RETRY_SECONDS = 10


def review_completed(payload: object, pr_url: str, head: str) -> bool:
    """Require complete results for this pull request and revision, regardless of findings."""
    if not isinstance(payload, dict) or payload.get("pr_url") != pr_url or payload.get("error"):
        return False
    session = payload.get("review_session")
    return (
        payload.get("qar_operation_result_truncated", False) is False
        and isinstance(payload.get("findings"), list)
        and type(payload.get("finding_count")) is int
        and payload.get("finding_count") == len(payload["findings"])
        and all(isinstance(finding, dict) for finding in payload["findings"])
        and isinstance(session, dict)
        and session.get("status") == "completed"
        and session.get("review_complete") is True
        and session.get("commit_sha") == head
        and type(session.get("run_id")) is int
        and session["run_id"] > 0
        and parse_utc_time(session.get("started_at")) is not None
    )


def parse_utc_time(value: object) -> datetime | None:
    """Parse the UTC timestamps shared by the completion and merge workflows."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.utcoffset() == timedelta(0) else None
    except ValueError:
        return None


def gh(*args: str) -> str:
    """Run GitHub CLI with arguments passed directly to the process."""
    return subprocess.run(["gh", *args], check=True, stdout=subprocess.PIPE, text=True).stdout


def github(path: str, *args: str) -> Any:
    """Read the JSON output of a GitHub API call."""
    return json.loads(gh("api", *args, path))


def publish(head: str, state: str, description: str, url: str) -> None:
    """Publish only on the commit whose review was inspected."""
    github(
        f"repos/{REPOSITORY}/statuses/{head}",
        "--method",
        "POST",
        "-f",
        f"state={state}",
        "-f",
        f"context={CONTEXT}",
        "-f",
        f"description={description}",
        "-f",
        f"target_url={url}",
    )


def reconcile(pr: int) -> None:
    """Wait briefly for stored review evidence, leaving missing evidence blocking."""
    path = f"repos/{REPOSITORY}/pulls/{pr}"
    pull = github(path)
    if pull["state"] != "open":
        return
    head = pull["head"]["sha"]
    pr_url = f"https://github.com/{REPOSITORY}/pull/{pr}"
    publish(head, "pending", "Waiting for a completed Qodo review of this commit", pr_url)
    deadline = time.monotonic() + 180
    try:
        for attempt in range(ATTEMPTS):
            payload = read_review(pr_url, deadline)
            live = github(path)
            if live["state"] != "open" or live["head"]["sha"] != head:
                return
            if review_completed(payload, pr_url, head):
                started = payload["review_session"]["started_at"]
                publish(head, "success", f"Qodo reviewed; started={started}", pr_url)
                return
            if attempt + 1 < ATTEMPTS:
                if time.monotonic() + RETRY_SECONDS >= deadline:
                    raise TimeoutError("Qodo read deadline exceeded")
                time.sleep(RETRY_SECONDS)
        print("Qodo review is missing, incomplete or stale; qodo-reviewed remains pending.")
    except Exception:
        # Vendor and transport exceptions can contain response bodies or credentials.
        publish(head, "error", "Could not verify Qodo review; retry the completion workflow", pr_url)
        raise RuntimeError("Could not verify Qodo review evidence") from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pr", type=int)
    args = parser.parse_args()
    if args.pr <= 0:
        parser.error("PR number must be positive")
    reconcile(args.pr)


if __name__ == "__main__":
    main()
