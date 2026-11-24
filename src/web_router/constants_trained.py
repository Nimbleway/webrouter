"""The trained router's name, data files, reasons and messages (:mod:`web_router.routing.trained`)."""

from __future__ import annotations

from typing import Literal

TRAINED_ROUTER_NAME = "trained"
"""The built-in router that picks a request's method from a table trained on labelled requests."""

TRAINED_DATA_DIR = ("data", "trained")
"""Where the trained router's release ships inside the package."""

TRAINED_MANIFEST_FILE = "manifest.json"
"""The release's manifest: its version, its schema and each data file with its checksum."""

TRAINED_SCHEMA = 1
"""The manifest schema this code reads. A release with another schema is refused, not half-read."""

TRAINED_LAST_RESORT: Literal["search"] = "search"
"""The method ranked last when the table's row does not name it: the one every setup can run."""

TRAINED_REASON = "trained {version}: {tool} for {features}"
"""The router's reason: its release, the method it ranked first, and the feature values that row was keyed on."""

TRAINED_REASON_OVERALL = "trained {version}: {tool}, the most common method overall"
"""The reason when no feature narrowed the row: the request matched nothing the table saw often enough."""

MSG_TRAINED_SCHEMA = "the trained router's manifest has schema {got!r}; this version of web-router reads schema {want}."

MSG_TRAINED_TABLE = "the trained router's tool table is not usable: {why}."
