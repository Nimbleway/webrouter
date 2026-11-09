"""What a browser handle's page actions return, and the options they take.

``goto``, ``snapshot``, ``click``, ``type``, ``press``, ``screenshot`` and
``read`` on :class:`~web_router.BrowserHandle` answer with these. Kept apart so
``types.py`` stops growing; this module imports nothing of ours.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

PageLoadState = Literal["commit", "domcontentloaded", "load", "networkidle"]
"""How far a page must get before ``goto`` returns. Left unset, Playwright waits for ``load``."""

PageFormat = Literal["text", "html"]
"""What :meth:`BrowserHandle.read <web_router.BrowserHandle.read>` returns: the rendered text, or the DOM as HTML."""


class PageState(BaseModel):
    """Where the page is after an action."""

    model_config = ConfigDict(extra="forbid")

    url: str
    title: str = ""
    status_code: int | None = Field(
        default=None,
        description=(
            "The HTTP status of the response a navigation produced. Set by ``goto`` only, and ``None`` when "
            "the navigation produced no response -- a same-document jump, or a page with no HTTP behind it."
        ),
    )


class PageSnapshot(BaseModel):
    """The page as an agent reads it: its accessibility tree, each element tagged with a ref.

    :attr:`tree` is Playwright's ``aria_snapshot(mode="ai")`` YAML, with unnamed
    wrapper containers and link-URL lines dropped to keep it short. Pass a ref
    back unchanged -- ``click(ref="e14")`` -- and treat it as opaque: its shape
    is not stable. A ref is good until the next snapshot or navigation, and one
    used after that raises :class:`~web_router.exceptions.StaleElementError`.
    """

    model_config = ConfigDict(extra="forbid")

    url: str
    title: str = ""
    tree: str = Field(description='One element per line, e.g. ``- button "Submit" [ref=e11]``.')
    truncated: bool = Field(
        default=False,
        description=(
            "Whether :attr:`tree` was cut at a line boundary to fit ``max_chars``. Refs past the cut "
            "were never offered, and using one raises StaleElementError."
        ),
    )


class Screenshot(BaseModel):
    """A PNG capture of the page. In JSON, :attr:`png` is base64, both ways."""

    model_config = ConfigDict(extra="forbid", ser_json_bytes="base64", val_json_bytes="base64")

    png: bytes = Field(repr=False, description="The image, as PNG bytes.")
    width: int = Field(description="Width of the image in pixels.")
    height: int = Field(description="Height of the image in pixels.")
    url: str = Field(description="The page it was taken of.")


class Viewport(BaseModel):
    """The size of the page a session renders, in CSS pixels."""

    model_config = ConfigDict(extra="forbid")

    width: Annotated[int, Field(ge=1, description="Width in pixels.")]
    height: Annotated[int, Field(ge=1, description="Height in pixels.")]
