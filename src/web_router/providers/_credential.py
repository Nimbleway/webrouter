"""Whether a provider's calls carry a credential, which ``auto`` reads before warning that one was refused."""

from __future__ import annotations

from typing import Any, Self

from web_router._environment import env_value, environment, reads_environment


class _CredentialMixin:
    """:attr:`holds_credential` for every provider: a key, else its ``env_api_key`` variable set."""

    api_key: str | None
    reads_environment: bool = True
    """Whether this provider was built reading the environment: a router with ``use_environment=False`` builds
    one that did not, and the switch is in force only while it builds."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Self:
        instance = super().__new__(cls)
        instance.reads_environment = reads_environment()
        return instance

    @property
    def holds_credential(self) -> bool:
        """Whether calls carry a credential: a key of its own, else its ``env_api_key`` variable set.

        ``auto`` warns about a 401 or 403 only from a member holding one: a keyless backend refused nothing of the
        caller's. A backend whose credential is not a key (a caller's SDK client, a signature) overrides this. One
        built not reading the environment never took its variable, so that variable does not count.
        """
        if self.api_key:
            return True
        env_var = getattr(self, "env_api_key", None)
        with environment(self.reads_environment):
            return isinstance(env_var, str) and bool(env_value(env_var))
