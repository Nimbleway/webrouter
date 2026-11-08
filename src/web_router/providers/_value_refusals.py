"""Refusing a value of a search option a backend maps, beyond the ``search_depth`` levels it serves."""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.exceptions import UnsupportedParameterError
from web_router.providers.base import SearchProvider
from web_router.types import SearchRequest


class RefusesValues(SearchProvider):
    """A search backend that maps an option but cannot serve every value of it.

    Listed before the backend's base class. A subclass implements
    :meth:`refused_options` and gives each option it may refuse a sentence in
    :attr:`refusal_reasons`. A refused value raises
    :class:`UnsupportedParameterError` naming it before anything is sent, and,
    because :meth:`refused_values` includes it, ``auto`` passes the backend over.
    """

    refusal_reasons: ClassVar[dict[str, str]] = {}
    """Option -> why this backend refuses the value it was set to, said after the value."""

    @classmethod
    def refused_options(cls, request: SearchRequest) -> dict[str, Any]:
        """Each mapped option whose value this backend cannot serve, by name, beside that value."""
        return {}

    @classmethod
    def refused_values(cls, request: SearchRequest) -> dict[str, Any]:
        return {**super().refused_values(request), **cls._mapped_refusals(request)}

    @classmethod
    def _mapped_refusals(cls, request: SearchRequest) -> dict[str, Any]:
        # An option this backend does not map at all is refused as an option, not by value.
        if request.specified_parameters() - cls.supported_parameters:
            return {}
        return cls.refused_options(request)

    def validate(self, request: SearchRequest) -> None:
        self._reject_unmapped(request)
        if refused := self._mapped_refusals(request):
            detail = " ".join(dict.fromkeys(self.refusal_reasons[name] for name in sorted(refused)))
            raise UnsupportedParameterError(self.name, refused, values=refused, detail=detail)
        super().validate(request)
