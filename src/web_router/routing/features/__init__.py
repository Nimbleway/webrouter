"""The built-in ``features`` router: a prompt's method from four features it reads, by a table learned offline.

Experimental: every name here may change before 1.0.
"""

from web_router.routing.features.mapping import FEATURE_MAPPING, Features
from web_router.routing.features.router import FeaturesRouter, read_features
from web_router.routing.features.sensors import Verb

__all__ = ["FEATURE_MAPPING", "Features", "FeaturesRouter", "Verb", "read_features"]
