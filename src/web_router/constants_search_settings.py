"""What a search backend needs beside its credential before it counts as configured.

``ENV_BY_PROVIDER`` names one variable per search backend, its credential; a
backend that also needs a setting is listed here, and is reported configured
only when every one is set.
"""

from __future__ import annotations

from web_router.constants import (
    ENV_BRIGHTDATA_ZONE,
    ENV_OXYLABS_USERNAME,
    PROVIDER_BRIGHTDATA,
    PROVIDER_OXYLABS,
)
from web_router.constants_litellm import LITELLM_SEARCH_SETTINGS_BY_PROVIDER

SEARCH_SETTINGS_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    **LITELLM_SEARCH_SETTINGS_BY_PROVIDER,
    PROVIDER_OXYLABS: (ENV_OXYLABS_USERNAME,),
    PROVIDER_BRIGHTDATA: (ENV_BRIGHTDATA_ZONE,),
}
"""Oxylabs' username beside its password, Bright Data's zone beside its token, a LiteLLM proxy's URL beside its key:
without the setting each would be listed, yet fail every call."""
