"""The built-in ``gemini`` router: Google's Gemini Decisions API, its preset, limit and question names.

Experimental. Kept apart from :mod:`web_router.constants_decider` so each stays small.
"""

from __future__ import annotations

GEMINI_ROUTER_NAME = "gemini"
"""The registered name of the built-in router on the Gemini Decisions API."""

GEMINI_DEFAULT_PRESET = "google"
"""The preset ``router="gemini"`` uses when ``router_config`` names neither a preset nor a ``base_url``."""

GEMINI_KEY_ENV = "GEMINI_API_KEY"
GEMINI_HOST = "generativelanguage.googleapis.com"

GEMINI_DEFAULT_PATH = "/v1beta/decisions"
GEMINI_PRESETS: dict[str, tuple[str, str, str, str]] = {
    GEMINI_DEFAULT_PRESET: (f"https://{GEMINI_HOST}", GEMINI_DEFAULT_PATH, "gemini-decision-1", GEMINI_KEY_ENV),
}
"""``gemini`` preset -> (base URL, path, pinned model, key variable)."""

GEMINI_AUTH_HEADER = "x-goog-api-key"
GEMINI_AUTH_VALUE = "{key}"
"""The API takes its key in its own header, never in ``Authorization`` or the URL."""

GEMINI_ROUTE_TOOLS = ("answer", "extract", "research", "browse")
"""The tools the ``gemini`` router orders by default: every tool but search, which it would slow down."""

GEMINI_MAX_OPTIONS = 255
"""The most options one ``choice`` question takes; the API refuses a request with more, whole."""

GEMINI_MIN_OPTIONS = 2
"""The fewest options one ``choice`` question takes: a lone option is not asked about, it is the only answer."""

GEMINI_BACKEND_QUESTION = "backend"
GEMINI_MODE_QUESTION = "mode"
"""The two questions a ``gemini`` request asks: which backend, then which vendor mode."""

GEMINI_MODE_INSTRUCTIONS = (
    " Each option here is a backend in one of its vendor modes: pick the mode that fits the request best."
)
"""Added to the instructions of the mode question."""
