"""Environment-configured backends, kept apart from the implementation registry."""

from __future__ import annotations

import os

from web_router.constants import (
    BROWSE_ENV_BY_PROVIDER,
    BROWSER_ENV_BY_PROVIDER,
    ENV_BY_PROVIDER,
    EXTRACT_ENV_BY_PROVIDER,
    RESEARCH_ENV_BY_PROVIDER,
)
from web_router.constants_litellm import LITELLM_SEARCH_SETTINGS_BY_PROVIDER as SEARCH_SETTINGS
from web_router.constants_live_data import LIVE_DATA_CREDENTIAL_SETS


def is_set(env_var: str) -> bool:
    """Whether ``env_var`` holds a value, counting whitespace-only as unset, as the providers do."""
    return bool((os.environ.get(env_var) or "").strip())


def configured_providers() -> list[str]:
    """Every builtin search provider you currently have a credential for, plus the keyless ones.

    Reports what the environment makes usable, which :func:`available_providers` deliberately does not: that
    lists every name the router knows, key or no key.

    This reads environment variables only. A key passed as ``provider_config`` is invisible here, and a key that is
    present may still be rejected by the service -- so treat this as "worth trying", not as a guarantee, and let the
    provider itself raise when a credential is actually missing or bad. A backend that needs a setting beside its
    credential -- a LiteLLM proxy's URL -- is reported only when it has both.
    """
    return sorted(
        name
        for name, env_var in ENV_BY_PROVIDER.items()
        if (env_var is None or is_set(env_var)) and all(map(is_set, SEARCH_SETTINGS.get(name, ())))
    )


def configured_extract_providers() -> list[str]:
    """Every builtin extract provider you currently have credentials for, plus the keyless ones.

    The same caveats as :func:`configured_providers`. A backend needing more than one variable -- Oxylabs its
    username and password, Bright Data its token and zone -- is reported only when it has all of them.
    """
    return sorted(
        name for name, env_vars in EXTRACT_ENV_BY_PROVIDER.items() if all(is_set(env_var) for env_var in env_vars)
    )


def configured_live_data_providers() -> list[str]:
    """Every builtin live data provider you currently have credentials for.

    The same caveats as :func:`configured_providers`. A live data run fetches and parses on the backend's account,
    so there is no keyless member and no ``auto``; with no key at all this is empty.
    """
    return sorted(name for name, sets in LIVE_DATA_CREDENTIAL_SETS.items() if any(all(map(is_set, e)) for e in sets))


def configured_research_providers() -> list[str]:
    """Every builtin research provider you currently have credentials for.

    The same caveats as :func:`configured_providers`. Shorter than the other two in practice: every research backend
    bills for the run, so there is no keyless member to pad it out, and with no key at all this is empty but for
    ``auto`` -- which will then say which variables would fix it.
    """
    return sorted(
        name for name, env_vars in RESEARCH_ENV_BY_PROVIDER.items() if all(is_set(env_var) for env_var in env_vars)
    )


def configured_browser_providers() -> list[str]:
    """Every builtin browser provider you currently have credentials for.

    The same caveats as :func:`configured_providers`. A cloud browser bills for every minute it is open; ``local`` needs
    no key and is always listed, whether or not a browser is installed for it to launch.
    """
    return sorted(
        name for name, env_vars in BROWSER_ENV_BY_PROVIDER.items() if all(is_set(env_var) for env_var in env_vars)
    )


def configured_browse_providers() -> list[str]:
    """Every builtin browse provider you currently have credentials for.

    The same caveats as :func:`configured_providers`. No keyless tier: every task backend bills for the run, so
    with no key at all this is empty but for ``auto`` -- which will then say which variables would fix it.
    """
    return sorted(
        name for name, env_vars in BROWSE_ENV_BY_PROVIDER.items() if all(is_set(env_var) for env_var in env_vars)
    )
