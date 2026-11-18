"""One tool call's outcome, as the Claude and OpenAI tools hand it back."""

from __future__ import annotations

from dataclasses import dataclass, field

from web_router.types import ExtractResponse, SearchResponse
from web_router.types_answer import AnswerResponse


@dataclass
class ToolResult:
    """One tool call's outcome: what the model reads, and everything it does not."""

    content: str
    """The text for the model -- a compact rendering, or a one-line error."""

    is_error: bool = False
    """True when the call did not run or failed; the model reads why and may try again."""

    response: SearchResponse | ExtractResponse | AnswerResponse | None = field(default=None, repr=False)
    """The untouched response, for the caller: the model reads only ``content``."""

    error: BaseException | None = field(default=None, repr=False)
    """The failure behind an error result, with the backend's own text, for the caller's logs.

    ``None`` when the model's own input was refused: nothing was sent.
    """

    @property
    def provider(self) -> str | None:
        """Who served the call -- for ``auto``, the member that answered."""
        return self.response.provider if self.response is not None else None
