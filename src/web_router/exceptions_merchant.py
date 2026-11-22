"""The errors the ``merchant`` backend raises for a store the registry does not serve or the call may not ask of it.

Kept apart from :mod:`web_router.exceptions`, which is at its size limit; it imports that module, never the reverse.
An unlisted domain is the shared :class:`~web_router.LiveDataSourceNotFoundError`.
"""

from __future__ import annotations

from web_router.constants_merchant import MSG_NO_ROUTE, MSG_NOT_VALIDATED
from web_router.exceptions import ProviderError
from web_router.types_errors import ERROR_KIND_BAD_REQUEST

__all__ = ["MerchantNotValidatedError", "MerchantRouteError"]


class MerchantRouteError(ProviderError):
    """The record has no route this client may run: none, several, an unsafe host, a quarantined or template store."""

    def __init__(self, provider: str, domain: str, snapshot_id: str, reason: str, provider_route: str) -> None:
        self.domain = domain
        super().__init__(
            provider,
            MSG_NO_ROUTE.format(domain=domain, provider=provider_route, snapshot_id=snapshot_id, reason=reason),
            kind=ERROR_KIND_BAD_REQUEST,
        )


class MerchantNotValidatedError(ProviderError):
    """The call asks for something the registry has no passing test for on this store, so it is refused, not run."""

    def __init__(self, provider: str, domain: str, parameters: set[str], test: str, status: str) -> None:
        self.domain = domain
        self.parameters = frozenset(parameters)
        super().__init__(
            provider,
            MSG_NOT_VALIDATED.format(domain=domain, test=test, what=", ".join(sorted(parameters)), status=status),
            kind=ERROR_KIND_BAD_REQUEST,
        )
