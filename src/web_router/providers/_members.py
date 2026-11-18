"""An auto provider's members, each built once on first use, even across threads."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any, Generic, TypeVar, cast

from web_router.exceptions import WebRouterError
from web_router.providers._chain import _member_options

_MemberT = TypeVar("_MemberT")


class _Members(dict[int, _MemberT], Generic[_MemberT]):
    """An auto provider's members by position, each built on first use.

    Built under a lock: threads sharing one router must not each build, and
    leak, a member of their own, of which ``close()`` would see only the last.
    """

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()

    def member(
        self,
        position: int,
        chain: Sequence[tuple[str, dict[str, Any]]],
        members_config: Mapping[str, Mapping[str, Any]],
        create: Callable[..., _MemberT],
    ) -> _MemberT:
        """The member at ``position`` of ``chain``, built by ``create`` the first time it is asked for."""
        with self._lock:
            if position not in self:
                name, options = chain[position]
                self[position] = create(name, **_member_options(members_config, name, options))
            return self[position]


class BuiltMembers:
    """:meth:`built_members` for an ``auto`` chain: what ``warmup()`` builds ahead of the first call."""

    def built_members(self) -> Iterator[Any]:
        """Each member of the chain that can be built now; one that cannot (a missing extra or key) is skipped."""
        chain = cast(Any, self)
        for position in range(len(chain.members)):
            try:
                yield chain._member(position)
            except (WebRouterError, ImportError):
                continue
