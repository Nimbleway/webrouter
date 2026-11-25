"""The data-driven router's name, data files, reasons and messages (:mod:`web_router.routing.data_driven`)."""

from __future__ import annotations

from typing import Literal

DATA_DRIVEN_ROUTER_NAME = "data-driven"
"""The built-in router that ranks web methods and providers from bundled data, with no routing model call."""

DATA_DRIVEN_DATA_DIR = ("data", "data_driven")
"""Where the data-driven router's release ships inside the package."""

DATA_DRIVEN_MANIFEST_FILE = "manifest.json"
"""The release's manifest: its version, its schema and each data file with its checksum."""

DATA_DRIVEN_SCHEMA = 1
"""The manifest schema this code reads. A release with another schema is refused, not half-read."""

DATA_DRIVEN_LAST_RESORT: Literal["search"] = "search"
"""The method ranked last when the table's row does not name it: the one every setup can run."""

DATA_DRIVEN_REASON = "data-driven {version}: {tool} for {features}"
"""The router's reason: its release, the method it ranked first, and the feature values that row was keyed on."""

DATA_DRIVEN_REASON_OVERALL = "data-driven {version}: {tool}, the most common method overall"
"""The reason when no feature narrowed the row: the request matched nothing the table saw often enough."""

DATA_DRIVEN_REASON_LAST_RESORT = (
    "data-driven {version}: search, which no training request with {features} ran, as the last resort"
)
"""The reason when every method of the row is held back: a browse not opted into, an extract with no URL."""

DATA_DRIVEN_REASON_BACKENDS = "{reason}; search backends {order}, by the {measured} measures"
"""The reason's tail when the router ranks search's backends from its provider table."""

DATA_DRIVEN_ORDERS = {
    "cheap": "cheapest first by benchmark prices",
    "fast": "fastest first",
    None: "by measured quality, the cheapest of the best first by benchmark prices",
}
"""How the search backends are ranked, by the preference: what :data:`DATA_DRIVEN_REASON_BACKENDS` says."""

DATA_DRIVEN_REASON_NO_BACKEND = "{reason}; {tool} has no backend here, so search runs"
"""The reason's middle when the row's first method cannot run and the call searches instead."""

DATA_DRIVEN_SEARCH_ID = "search:"
"""What every provider-table row's candidate id starts with: the table measures search alone."""

MSG_DATA_DRIVEN_PROVIDERS = "the data-driven router's provider table is not usable: {why}."

MSG_DATA_DRIVEN_POINT = (
    "the row {row!r} needs a search candidate's id and four measures, each a finite number of 0 or more"
)
MSG_DATA_DRIVEN_POINT_ID = "it names {ids} more than once"
MSG_DATA_DRIVEN_PROVIDERS_READ = "it could not be read ({error})"

MSG_DATA_DRIVEN_BROWSE = "the data-driven router takes browse=True or browse=False; got {got!r}."

MSG_DATA_DRIVEN_SCHEMA = (
    "the data-driven router's manifest has schema {got!r}; this version of web-router reads schema {want}."
)

MSG_DATA_DRIVEN_TABLE = "the data-driven router's tool table is not usable: {why}."

MSG_DATA_DRIVEN_UNREADABLE = "its release files could not be read ({error}); router=None runs without it"
MSG_DATA_DRIVEN_ROW_TOTAL = "a row's total must equal the sum of its method counts"
MSG_DATA_DRIVEN_CHECKSUM = "its checksum does not match the manifest"
MSG_DATA_DRIVEN_MIN_COUNT = "min_count must be a positive integer"
MSG_DATA_DRIVEN_FEATURES = "it names features this version cannot compute"
MSG_DATA_DRIVEN_METHODS = "it names methods web-router does not route"
MSG_DATA_DRIVEN_TABLE_DEPTH = "it needs one table per key length"
MSG_DATA_DRIVEN_ROW_WIDTH = "a row needs one count per method and their total"
MSG_DATA_DRIVEN_ROOT = "it has no row for every request"
