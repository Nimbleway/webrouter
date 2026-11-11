"""What :meth:`~web_router.WebRouter.search` lists beside the web results: data APIs that match the query.

Kept apart from :mod:`web_router.types` so that file stops growing; it imports
this, never the reverse.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DataApiMatch(BaseModel):
    """One catalog endpoint whose words match a search query: found locally, never called.

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


DATA_APIS_DESCRIPTION = (
    "Catalog endpoints whose words match the query, best first: found locally at no cost, never called. "
    "Empty when search ran with data_apis=False or nothing matched well enough."
)
