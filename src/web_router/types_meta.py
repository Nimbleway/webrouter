"""What the router adds about a call beside the backend's answer: ``meta``, the presets that applied.

Apart from :mod:`web_router.types` so that file stops growing, as
:mod:`web_router.types_decision` is; it imports nothing of ours. Every
response a preset can apply to carries one in ``meta``, and jobs and browser
handles pin theirs.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PresetComponent = Literal["search", "extract", "templates", "research", "browse", "browser", "answer"]
"""A part of the router a preset can configure, one per method: ``templates`` is
``run_template``, ``browse`` is ``browse`` and ``submit_browse``, ``browser`` is
``open_browser``, ``research`` is ``research`` and ``submit_research``."""

PresetSource = Literal["request", "client"]
"""Where a preset came from: ``preset=`` on the call, or ``preset=`` on the router."""

PresetValidationState = Literal["pending", "passed", "partial"]
"""How far a call's preset checks have got.

``pending`` while a job has been accepted but has not finished: acceptance is
not completion. ``passed`` once the call returned under the preset's settings.
``partial`` is reserved for completion checks a preset declares and the result
did not meet; no preset can declare one yet, so nothing returns it today.
"""


class ResolvedPreset(BaseModel):
    """One preset that applied to a call: where it came from, and which exact version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: PresetSource
    id: str
    version: str

    @property
    def ref(self) -> str:
        """The exact reference, ``id@version``."""
        return f"{self.id}@{self.version}"


class PresetValidation(BaseModel):
    """The state of a call's preset checks, and any requirement left unmet."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: PresetValidationState
    issues: list[str] = Field(default_factory=list)


class PresetMeta(BaseModel):
    """What presets did to one call: which applied, and the settings they supplied.

    :attr:`effective` holds only what a preset supplied and the call kept; a
    value the caller passed is never copied here. A preset's
    ``provider_options`` appears by its keys alone, since a backend's own
    options can hold a key. :attr:`overridden` names the preset settings the
    caller's own arguments replaced.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    component: PresetComponent
    resolved: list[ResolvedPreset] = Field(
        default_factory=list, description="Every preset that applied, the per-call one first, then the client's."
    )
    effective: dict[str, Any] = Field(
        default_factory=dict, description="The settings the presets supplied that this call used."
    )
    overridden: list[str] = Field(
        default_factory=list, description="Preset settings the call's own arguments replaced, by name."
    )
    validation: PresetValidation


class ResponseMeta(BaseModel):
    """What the router adds about a call beside the backend's answer. Only presets so far."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    preset: PresetMeta | None = None

    def at(self, state: PresetValidationState) -> ResponseMeta:
        """A deep copy of this metadata with the preset checks at ``state`` -- a job's, once it has finished.

        A copy every time, so a caller who changes what it was handed cannot
        change what the job or session it came from reports next.
        """
        if self.preset is None:
            return self.model_copy(deep=True)
        validation = PresetValidation(state=state)
        return self.model_copy(
            deep=True, update={"preset": self.preset.model_copy(deep=True, update={"validation": validation})}
        )


META_DESCRIPTION = "What the router adds about the call: the presets that applied. None when no preset did."
