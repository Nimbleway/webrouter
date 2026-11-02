"""Choose a method for a query, and the params to call it with, before anything is spent.

The choice is a placeholder until the feature-vector lookup lands: extract when
the query holds a URL, search otherwise.
"""

from __future__ import annotations

import re

from web_router.types import Budgets, ClassifyRequest, Method, MethodParams, Route

_URL = re.compile(r"https?://\S")


def classify(query: str, budgets: Budgets | None = None) -> Route:
    """The route for ``query``: see :meth:`~web_router.WebRouter.classify`."""
    request = ClassifyRequest(query=query, budgets=budgets)
    method: Method = "extract" if _URL.search(request.query) else "search"
    return Route(query=request.query, method=method, method_params=MethodParams.model_validate({method: {}}))
