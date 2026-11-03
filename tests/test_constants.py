from __future__ import annotations

import pytest
from pydantic import BaseModel

from web_router.constants import _described


class _Undocumented(BaseModel):
    query: str


def test_an_mcp_argument_without_a_description_fails_instead_of_publishing_null() -> None:
    with pytest.raises(ValueError, match=r"_Undocumented\.query"):
        _described(_Undocumented, "query")
