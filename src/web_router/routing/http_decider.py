"""The setup the built-in deciders on a decision API share: preset, endpoint, key, model and timeout.

Experimental. A subclass names its router, presets, default path, preset and
route tools, and the header its key travels in, and writes its own ``body`` and
``adecide``; :func:`~.decider_router.takes_config_of` reads this constructor's
signature, so every such router takes the same ``router_config`` keys.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any, ClassVar

from web_router.constants_decider import HTTP_DECIDER_TIMEOUT_S
from web_router.constants_remote import REMOTE_AUTH_HEADER, REMOTE_AUTH_VALUE
from web_router.routing.decider_http import endpoint, preset_of, text


class HttpDecider:
    """A decider that posts one question per routed call to a decision API, never retried."""

    router_name: ClassVar[str]
    presets: ClassVar[dict[str, Any]]
    default_path: ClassVar[str]
    default_route_tools: ClassVar[Collection[str]]
    default_preset: ClassVar[str | None] = None
    """The preset used when neither ``preset`` nor ``base_url`` is given; ``None`` makes one of them required."""
    auth: ClassVar[tuple[str, str]] = (REMOTE_AUTH_HEADER, REMOTE_AUTH_VALUE)

    route_modes = False
    """Shown backends only until modes are on by default; a caller's ``route_modes=True`` shows it each mode too."""

    def __init__(
        self,
        *,
        preset: str | None = None,
        base_url: str | None = None,
        path: str | None = None,
        model: str | None = None,
        api_key_env: str | None = None,
        api_key: str | None = None,
        timeout_s: float = HTTP_DECIDER_TIMEOUT_S,
        route_tools: Collection[str] | None = None,
    ) -> None:
        """
        Raises:
            ConfigurationError: for an unknown preset, a preset and a base URL together, neither when the router
                has no default preset, no model, or any endpoint or key :func:`~.decider_http.endpoint` refuses.
        """
        name = self.router_name
        named = self.default_preset if preset is None and base_url is None else preset
        row = preset_of(name, self.presets, named, base_url)
        if row is not None:
            base_url, preset_path, preset_model, default_env = row
            path = preset_path if path is None else path
            model = preset_model if model is None else model
            api_key_env = default_env if api_key_env is None else api_key_env
        self.model = text(model, name, "model")
        route = text(self.default_path if path is None else path, name, "path")
        self._endpoint = endpoint(
            name,
            base_url,
            "/" + route.lstrip("/"),
            api_key=api_key,
            api_key_env=api_key_env,
            timeout_s=timeout_s,
            auth=self.auth,
        )
        self.timeout_s = self._endpoint.timeout_s
        self.name = f"{name}({self._endpoint.domain})"
        self.route_tools = self.default_route_tools if route_tools is None else route_tools

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._endpoint.host!r}, model={self.model!r}, timeout_s={self.timeout_s})"
