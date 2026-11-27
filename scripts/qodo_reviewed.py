"""Publish review completion without treating open findings as approval."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
from typing import Any

import httpx2
from mcp.client.streamable_http import streamable_http_client

from mcp import ClientSession

QODO_ENDPOINT = "https://sdk.qodo.ai/v1/tools/mcp/"
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
        and payload.get("finding_count") == len(payload["findings"])
        and all(isinstance(finding, dict) for finding in payload["findings"])
        and isinstance(session, dict)
        and session.get("status") == "completed"
        and session.get("review_complete") is True
        and session.get("commit_sha") == head
        and session.get("run_id") is not None
    )


def github(path: str, *args: str) -> Any:
    """Call GitHub without interpreting pull-request data as shell code."""
    result = subprocess.run(["gh", "api", path, *args], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


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


async def read_review(session: ClientSession, pr_url: str) -> object:
    """Read stored results from Qodo; never request a new review."""
    result = await session.call_tool("get-pr-review-findings", {"pr_url": pr_url})
    if result.isError:
        raise RuntimeError("Qodo could not return review evidence")
    if result.structuredContent is not None:
        return result.structuredContent
    texts = [block.text for block in result.content if block.type == "text"]
    if len(texts) != 1:
        raise RuntimeError("Qodo returned an unsupported review response")
    return json.loads(texts[0])


async def reconcile(pr: int) -> None:
    """Wait briefly for stored review evidence, leaving missing evidence blocking."""
    path = f"repos/{REPOSITORY}/pulls/{pr}"
    pull = github(path)
    if pull["state"] != "open":
        return
    head = pull["head"]["sha"]
    pr_url = f"https://github.com/{REPOSITORY}/pull/{pr}"
    publish(head, "pending", "Waiting for a completed Qodo review of this commit", pr_url)
    try:
        async with (
            asyncio.timeout(180),
            httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {os.environ['QODO_API_KEY']}"},
                follow_redirects=False,
            ) as client,
            streamable_http_client(QODO_ENDPOINT, http_client=client) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            for attempt in range(ATTEMPTS):
                payload = await read_review(session, pr_url)
                live = github(path)
                if live["state"] != "open" or live["head"]["sha"] != head:
                    return
                if review_completed(payload, pr_url, head):
                    publish(head, "success", "Qodo finished reviewing this commit; findings may remain", pr_url)
                    return
                if attempt + 1 < ATTEMPTS:
                    await asyncio.sleep(RETRY_SECONDS)
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
    asyncio.run(reconcile(args.pr))


if __name__ == "__main__":
    main()
