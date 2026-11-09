"""The errors a preset raises, every one before the backend is called.

Listed in the tree of :mod:`web_router.exceptions`, which does not import it;
apart so that file stays under the length rule, as :mod:`web_router.exceptions_budget` is.
"""

from __future__ import annotations

from collections.abc import Iterable

from web_router.constants_presets import MSG_PRESET_INCOMPATIBLE, MSG_PRESET_NOT_FOUND, MSG_PRESET_VERSIONS
from web_router.exceptions import WebRouterError
from web_router.types_presets import PresetIssue


class PresetError(WebRouterError):
    """A preset could not be used. Every one is raised before the backend is called, so none bills."""


class PresetNotFoundError(PresetError):
    """No preset is loaded under that exact ``id@version``.

    Nothing is fetched or installed to find one: definitions come only from
    what was loaded or passed in. :attr:`versions` lists the versions of the
    same id that are loaded, so a caller who asked for the wrong one can see
    the right one.
    """

    def __init__(self, ref: str, versions: Iterable[str] = ()) -> None:
        self.ref = ref
        self.versions = sorted(versions)
        preset_id = ref.partition("@")[0]
        listed = MSG_PRESET_VERSIONS.format(id=preset_id, versions=", ".join(self.versions)) if self.versions else ""
        super().__init__(MSG_PRESET_NOT_FOUND.format(ref=ref, versions=listed))


class PresetCompatibilityError(PresetError):
    """A per-call preset does not apply to the method it was passed to.

    Only a per-call preset raises this. A router's own preset that does not
    apply to a method simply does not apply: the call runs as if it had none.
    """

    def __init__(self, ref: str, component: str, applies_to: Iterable[str]) -> None:
        self.ref = ref
        self.component = component
        self.applies_to = list(applies_to)
        super().__init__(
            MSG_PRESET_INCOMPATIBLE.format(ref=ref, component=component, applies_to=", ".join(self.applies_to))
        )


class PresetValidationError(PresetError):
    """A definition is malformed, a value is wrong for its setting or a call replaced a locked setting.

    :attr:`issues` names each problem by field, so an agent can correct the one
    it got wrong without a person reading the message.
    """

    def __init__(self, issues: Iterable[PresetIssue], *, lead: str) -> None:
        """
        Args:
            lead: What was being checked, as the sentence the issues follow --
                from :data:`~web_router.constants_presets.MSG_PRESET_INVALID`,
                ``MSG_PRESET_DEFINITION`` or ``MSG_PRESET_CONFLICT``.
        """
        self.issues = list(issues)
        listed = "; ".join(f"{issue.field}: {issue.message}" for issue in self.issues)
        super().__init__(f"{lead}: {listed}")
