"""A preset: one router method and the optional arguments saved for it."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from web_router.constants_presets import (
    PRESET_DESCRIPTION_MAX_LENGTH,
    PRESET_DESCRIPTION_PATTERN,
    PRESET_NAME_DESCRIPTION,
    PRESET_NAME_MAX_LENGTH,
    PRESET_NAME_PATTERN,
    PRESET_PARAMS_DESCRIPTION,
)
from web_router.presets.params import PRESET_TOOLS, PresetTool, preset_params

PresetName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=PRESET_NAME_MAX_LENGTH,
        pattern=PRESET_NAME_PATTERN,
        description=PRESET_NAME_DESCRIPTION,
    ),
]


class Preset(BaseModel):
    """One router method and the optional arguments saved for it, under a name any call of the method can use.

    ``params`` are the method's own optional keyword arguments -- ``search``'s
    ``max_results`` and ``include_domains``, ``run_template``'s ``format``
    (never its ``provider``, which the call names) -- held to the same types and limits as a call that sets
    them, so a preset is exactly as strict as calling the method directly. A
    key the method does not take is refused by name, with the list of what it
    does take. The call's own input -- the query, the URLs, the task, the
    template, research's ``records`` -- is never a param, and neither is ``budget``, which caps one
    call. ``params`` holds the JSON form, the form every store keeps: a
    ``viewport`` is a mapping, ``provider`` a name. Pass ``preset=<name>`` to
    the method to use it; the call's own arguments override the preset's.

    A preset has no version and is never edited or deleted through a store:
    a different setting is a preset with a different name.

    Example:
        >>> Preset(name="fresh_news", tool="search", params={"search_depth": "fast", "max_results": 10})
    """

    model_config = ConfigDict(extra="forbid")

    name: PresetName
    tool: PresetTool = Field(description="The router method the preset runs, named as the method is.")
    params: dict[str, Any] = Field(default_factory=dict, description=PRESET_PARAMS_DESCRIPTION)
    description: str | None = Field(
        default=None, max_length=PRESET_DESCRIPTION_MAX_LENGTH, pattern=PRESET_DESCRIPTION_PATTERN
    )

    @model_validator(mode="before")
    @classmethod
    def _params_the_tool_takes(cls, data: Any) -> Any:
        # Checked before the fields, as the tool's own model: its errors then name the param, not `params`.
        if isinstance(data, dict) and data.get("tool") in PRESET_TOOLS:
            return {**data, "params": preset_params(data["tool"], data.get("params") or {})}
        return data
