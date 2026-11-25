"""The data-driven router: a request's method and search's backends from shipped, versioned tables, with no model call.

:mod:`.patterns` and :mod:`.features` read the request, :mod:`.table` holds the release's tool table,
:mod:`.providers` its measured search candidates and :mod:`.lines` the order they are tried in, and :mod:`.router`
is the router ``router="data-driven"`` builds. A release is ``data/data_driven/manifest.json`` and the files it
names; ``make data-driven-router`` writes one from a training run's output.
"""

from web_router.routing.data_driven.router import DataDrivenRouter

__all__ = ["DataDrivenRouter"]
