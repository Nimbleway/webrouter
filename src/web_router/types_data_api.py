"""What ``search`` and ``extract`` list beside their results: data APIs that match the query or the page's site.

Kept apart from :mod:`web_router.types` so that file stops growing; it imports
this, never the reverse.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DataApiMatch(BaseModel):
    """One catalog endpoint that matches a search query or an extracted page's site: found locally, never called.

    Run it with :meth:`~web_router.WebRouter.call_vendor` and :attr:`endpoint`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    endpoint: str = Field(description="The endpoint id ('<data API>.<job>'), the name call_vendor takes.")
    data_api: str = Field(description="The data API the endpoint belongs to, as the catalog names it.")
    name: str = Field(description="What the call does, as a short action phrase.")
    summary: str = ""
    kind: Literal["read", "write"] = Field(description="'write' when the call changes anything on the API's side.")
    docs_url: str
    configured: bool = Field(description="Every credential the call requires is set.")
    missing: list[str] = Field(
        default_factory=list, description="The variable to set for each required credential that is not, first choice."
    )

    def brief(self, limit: int = 300) -> dict[str, Any]:
        """This match for a model's context: its text fields cut to ``limit`` characters, the docs URL left out.

        A catalog file from ``WEB_ROUTER_VENDOR_CATALOG`` can carry a summary of
        any length, and the MCP search tool's budget only trims web results.
        """
        row = self.model_dump(exclude={"docs_url"})
        for field in ("name", "summary"):
            if len(row[field]) > limit:
                row[field] = row[field][: limit - 1] + "\u2026"
        return row


DATA_APIS_DESCRIPTION = (
    "Catalog endpoints whose words match the query, best first: found locally at no cost, never called. "
    "Empty when search ran with data_apis=False or nothing matched well enough."
)

DATA_API_SITE_DESCRIPTION = (
    "Read endpoints of the data APIs that serve this page's site, those named by the URL's path first: found "
    "locally at no cost, never called. Empty when extract ran with data_apis=False or no catalog entry serves the site."
)
