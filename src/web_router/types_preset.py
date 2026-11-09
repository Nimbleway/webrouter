"""The preset a response was made with.

Kept apart so ``types.py`` stops growing; this module imports nothing of ours.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PresetApplied(BaseModel):
    """Which preset a call ran with, and the optional arguments that ran: the preset's, with the call's on top.

    The call's own input -- the query, the URLs, the task, the template -- is
    the call's and not repeated here. A value with no JSON form, such as a
    provider instance, is shown as its text.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="The preset's name.")
    params: dict[str, Any] = Field(default_factory=dict, description="The optional arguments the call ran with.")


PRESET_APPLIED_DESCRIPTION = "The preset the call ran with and the arguments that ran; None for a call without one."
