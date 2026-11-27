"""Read a GitHub URL with its README or conversation, using the shipped Data API endpoints."""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from web_router.exceptions import ProviderError
from web_router.exceptions_typed import InvalidParameterError
from web_router.vendors.types import VendorResponse

GitHubKind = Literal["repository", "issue", "pull_request"]

_GITHUB = "github"
_HOST = "github.com"
_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_REPO = r"(?!\.{1,2}(?:/|$))[A-Za-z0-9_.-]{1,100}"
_PATH = re.compile(
    rf"/(?P<owner>{_OWNER})/(?P<repo>{_REPO})(?:/(?P<kind>issues|pull)/(?P<number>[1-9][0-9]{{0,9}}))?/?"
)
_REPO_ENDPOINT = "github.repos.get"
_README_ENDPOINT = "github.repos.readme"
_ISSUE_ENDPOINT = "github.issues.get"
_PULL_ENDPOINT = "github.pulls.get"
_COMMENTS_ENDPOINT = "github.issues.comments"
_COMMENT_PAGE_SIZE = 100
_URL_ERROR = "expected an HTTPS github.com repository, issue or pull request URL"


class GitHubReadResponse(BaseModel):
    """The resource and its default-branch README or first page of conversation comments."""

    model_config = ConfigDict(extra="forbid")

    url: str
    kind: GitHubKind
    resource: dict[str, Any]
    readme: str | None = None
    comments: list[dict[str, Any]] = Field(default_factory=list)
    comments_truncated: bool = False


@dataclass(frozen=True)
class GitHubTarget:
    """Validated GitHub resource and parameters for its two catalog calls."""

    url: str
    kind: GitHubKind
    owner: str
    repo: str
    number: int | None

    @property
    def resource_call(self) -> tuple[str, dict[str, Any]]:
        if self.kind == "repository":
            return _REPO_ENDPOINT, {"owner": self.owner, "repo": self.repo}
        if self.kind == "issue":
            return _ISSUE_ENDPOINT, {"owner": self.owner, "repo": self.repo, "issue_number": self.number}
        return _PULL_ENDPOINT, {"owner": self.owner, "repo": self.repo, "pull_number": self.number}

    @property
    def context_call(self) -> tuple[str, dict[str, Any]]:
        params: dict[str, Any] = {"owner": self.owner, "repo": self.repo}
        if self.kind == "repository":
            return _README_ENDPOINT, params
        return _COMMENTS_ENDPOINT, {**params, "issue_number": self.number, "per_page": _COMMENT_PAGE_SIZE}


def github_target(url: str) -> GitHubTarget:
    """Validate the URL and build endpoint arguments without passing user-controlled paths to GitHub."""
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise InvalidParameterError("url", _URL_ERROR) from exc
    match = _PATH.fullmatch(parsed.path)
    if parsed.scheme != "https" or parsed.netloc.lower() != _HOST or match is None:
        raise InvalidParameterError("url", _URL_ERROR)
    owner, repo, section, number = match.groups()
    if section is None:
        return GitHubTarget(url, "repository", owner, repo, None)
    return GitHubTarget(url, "issue" if section == "issues" else "pull_request", owner, repo, int(number))


def optional_context(target: GitHubTarget, error: ProviderError) -> None:
    """An empty repository can legitimately have no default-branch README."""
    if target.kind != "repository" or error.status_code != httpx.codes.NOT_FOUND:
        raise error


def github_read_response(
    target: GitHubTarget, resource: VendorResponse, context: VendorResponse | None
) -> GitHubReadResponse:
    """Keep GitHub's resource and comment fields, and decode the README text."""
    if not isinstance(resource.data, dict):
        raise ProviderError(_GITHUB, "expected a GitHub resource object")
    result = GitHubReadResponse(url=target.url, kind=target.kind, resource=resource.data)
    if context is None:
        return result
    if target.kind == "repository":
        result.readme = _readme(context)
        return result
    comments = context.data
    if not isinstance(comments, list) or not all(isinstance(comment, dict) for comment in comments):
        raise ProviderError(_GITHUB, "expected a GitHub comment list")
    result.comments = comments
    pagination = httpx.Response(200, headers=context.headers).links
    result.comments_truncated = "next" in pagination
    return result


def _readme(response: VendorResponse) -> str:
    data = response.data
    if not isinstance(data, dict) or data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
        raise ProviderError(_GITHUB, "expected GitHub's base64-encoded README")
    try:
        return base64.b64decode("".join(data["content"].split()), validate=True).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError) as exc:
        raise ProviderError(_GITHUB, "GitHub returned invalid README base64", original=exc) from exc
