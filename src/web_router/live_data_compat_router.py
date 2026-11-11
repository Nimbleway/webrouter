"""The routers' live data methods under the names they had as templates, each warning and calling its replacement.

Kept apart from :mod:`web_router.client_live_data`: these go when the old
names do. :class:`SyncTemplateMethods` and :class:`AsyncTemplateMethods` are
mixed into the routers' live data classes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from web_router.live_data_compat import warn_renamed

if TYPE_CHECKING:
    from web_router.client_live_data import AsyncLiveData, SyncLiveData
    from web_router.providers.base import LiveDataProvider
    from web_router.types import Provider
    from web_router.types_live_data import LiveDataResponse, LiveDataSource, LiveDataSourcePage


class SyncTemplateMethods:
    """The sync router's old names."""

    def get_template_provider(self, name: str | Provider | LiveDataProvider) -> LiveDataProvider:
        """Deprecated: :meth:`get_live_data_provider`."""
        warn_renamed("get_template_provider", "get_live_data_provider", stacklevel=3)
        return cast("SyncLiveData", self).get_live_data_provider(name)

    def templates(self, **kwargs: Any) -> LiveDataSourcePage:
        """Deprecated: :meth:`live_data_sources`."""
        warn_renamed("templates", "live_data_sources", stacklevel=3)
        return cast("SyncLiveData", self).live_data_sources(**kwargs)

    def template(self, name: str, **kwargs: Any) -> LiveDataSource:
        """Deprecated: :meth:`live_data_source`."""
        warn_renamed("template", "live_data_source", stacklevel=3)
        return cast("SyncLiveData", self).live_data_source(name, **kwargs)

    def run_template(self, template: str, inputs: Mapping[str, Any] | None = None, **kwargs: Any) -> LiveDataResponse:
        """Deprecated: :meth:`live_data`."""
        warn_renamed("run_template", "live_data", stacklevel=3)
        return cast("SyncLiveData", self).live_data(template, inputs, **kwargs)


class AsyncTemplateMethods:
    """The async router's old names."""

    def get_template_provider(self, name: str | Provider | LiveDataProvider) -> LiveDataProvider:
        """Deprecated: :meth:`get_live_data_provider`."""
        warn_renamed("get_template_provider", "get_live_data_provider", stacklevel=3)
        return cast("AsyncLiveData", self).get_live_data_provider(name)

    async def templates(self, **kwargs: Any) -> LiveDataSourcePage:
        """Deprecated: :meth:`live_data_sources`."""
        warn_renamed("templates", "live_data_sources", stacklevel=3)
        return await cast("AsyncLiveData", self).live_data_sources(**kwargs)

    async def template(self, name: str, **kwargs: Any) -> LiveDataSource:
        """Deprecated: :meth:`live_data_source`."""
        warn_renamed("template", "live_data_source", stacklevel=3)
        return await cast("AsyncLiveData", self).live_data_source(name, **kwargs)

    async def run_template(
        self, template: str, inputs: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> LiveDataResponse:
        """Deprecated: :meth:`live_data`."""
        warn_renamed("run_template", "live_data", stacklevel=3)
        return await cast("AsyncLiveData", self).live_data(template, inputs, **kwargs)
