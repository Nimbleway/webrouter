"""The trained router: a request's method from shipped, versioned tables, with no model call.

:mod:`.patterns` and :mod:`.features` read the request, :mod:`.table` holds the release's tool table, and
:mod:`.router` is the router ``router="trained"`` builds. A release is ``data/trained/manifest.json`` and the
files it names; ``make trained-router`` writes one from a training run's output.
"""

from web_router.routing.trained.router import TrainedRouter

__all__ = ["TrainedRouter"]
