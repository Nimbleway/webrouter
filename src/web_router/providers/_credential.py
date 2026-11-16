"""Whether a provider's calls carry a credential, which ``auto`` reads before warning that one was refused."""

from __future__ import annotations

import os


class _CredentialMixin:
    """:attr:`holds_credential` for every provider: a key, else its ``env_api_key`` variable set."""

    api_key: str | None

    @property
    def holds_credential(self) -> bool:
        """Whether calls carry a credential: a key of its own, else its ``env_api_key`` variable set.

        ``auto`` warns about a 401 or 403 only from a member holding one: a keyless backend refused nothing of the
        caller's. A backend whose credential is not a key (a caller's SDK client, a signature) overrides this.
        """
        if self.api_key:
            return True
        env_var = getattr(self, "env_api_key", None)
        return isinstance(env_var, str) and bool(os.environ.get(env_var, "").strip())
