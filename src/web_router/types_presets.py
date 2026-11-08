"""A preset's definition, and the report :meth:`~web_router.PresetRegistry.validate` returns.

Kept apart so ``types.py`` stops growing. It imports ``types``, never the other way
round; what a response carries about the presets that applied, :class:`~web_router.PresetMeta`,
lives in ``types.py`` because every response model names it.

A preset is saved configuration for one or more components, and nothing else:
the definition is data that runs no code and calls nothing, and its settings are
the method's own arguments under their own names.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from web_router.types import PresetComponent, ResolvedPreset

__all__ = ["PRESET_ID_PATTERN", "PRESET_VERSION_PATTERN", "ComponentSettings", "Preset", "PresetCheck", "PresetIssue"]

PRESET_ID_PATTERN = r"[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)+"
"""``namespace.name``, lowercase: at least one dot, so every id says whose it is (``acme.supplier-research``)."""

PRESET_VERSION_PATTERN = r"\d+\.\d+\.\d+"
"""An exact ``major.minor.patch`` version. Ranges and ``latest`` are refused: a call never upgrades on its own."""


class ComponentSettings(BaseModel):
    """One component's section of a preset: the settings it supplies, and which a caller may replace.

    :attr:`defaults` are the method's own arguments, under their own names --
    ``max_results`` for a search, ``format`` for an extract. Which names a
    section may hold is :data:`~web_router.constants_presets.PRESET_FIELDS`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    defaults: dict[str, Any] = Field(default_factory=dict, description="Settings applied when the call sets none.")
    overridable: list[str] = Field(
        default_factory=list,
        description=(
            "The defaults a call or a per-call preset may replace. Every default left out is locked, "
            "and replacing a locked one with another value raises."
        ),
    )

    def locks(self, field: str) -> bool:
        """Whether this section sets ``field`` and refuses to let anything replace it."""
        return field in self.defaults and field not in self.overridable


class Preset(BaseModel):
    """A versioned, reusable configuration for one or more of the router's methods.

    Load definitions from JSON or YAML with
    :meth:`~web_router.PresetRegistry.load`, or build one here and pass it as
    ``preset=``. A version is immutable once loaded: a different definition
    under the same ``id@version`` is refused, so a pinned call never changes
    under its caller.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    spec_version: Literal[1] = Field(description="The manifest version. 1 is the only one.")
    id: str = Field(
        pattern=rf"^{PRESET_ID_PATTERN}$", description="namespace.name, lowercase -- 'acme.supplier-research'."
    )
    version: str = Field(pattern=rf"^{PRESET_VERSION_PATTERN}$", description="An exact major.minor.patch version.")
    title: str | None = None
    description: str | None = None
    applies_to: list[PresetComponent] = Field(min_length=1, description="The components this preset configures.")
    components: dict[PresetComponent, ComponentSettings] = Field(
        default_factory=dict, description="Each component's settings, keyed by component."
    )

    @property
    def ref(self) -> str:
        """The exact reference a call passes as ``preset=``: ``id@version``."""
        return f"{self.id}@{self.version}"

    def section(self, component: PresetComponent) -> ComponentSettings:
        """The settings this preset holds for ``component``; empty when it applies with none."""
        return self.components.get(component, ComponentSettings())


class PresetIssue(BaseModel):
    """One thing that would stop a call: where it is, and what is wrong."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str = Field(description="Where the problem is: 'preset', a setting's name or 'provider'.")
    message: str


class PresetCheck(BaseModel):
    """What a call would run with, checked without running it.

    :meth:`~web_router.PresetRegistry.validate` builds it from the definitions
    and the backend's class alone: nothing is sent, no client is built and no
    key is read. :attr:`ok` is true when nothing found would stop the call
    before it reached the backend; the backend can still refuse it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    component: PresetComponent
    provider: str | None = Field(default=None, description="The backend the call would go to, when one resolved.")
    resolved: list[ResolvedPreset] = Field(default_factory=list)
    effective: dict[str, Any] = Field(default_factory=dict)
    overridden: list[str] = Field(default_factory=list)
    issues: list[PresetIssue] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether nothing checked stops the call: no preset problem, bad argument or refusal by the backend's class."""
        return not self.issues
