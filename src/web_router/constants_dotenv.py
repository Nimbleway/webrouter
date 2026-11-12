"""What a :class:`~web_router.ConfigurationError` adds when the key it asks for sits in an unread ``.env``.

Kept apart from :mod:`web_router.constants`, so that file stops growing.
"""

from __future__ import annotations

DOTENV_FILENAME = ".env"
"""The file the hint looks for, in the working directory and then each parent."""

DOTENV_MAX_BYTES = 1 << 20
"""A ``.env`` larger than this is not read for the hint: no key file is that big."""

MSG_DOTENV_PREFIX = " A .env exists at "
"""How the hint starts, and how an error already carrying it is told apart (an unpickled one)."""

MSG_DOTENV_NOT_READ = (
    MSG_DOTENV_PREFIX + "{path} and sets {env_var}, but the library does not read it: the library reads "
    "the process environment only. Load the file first -- run with `uv run --env-file {path} ...`, or call "
    "`load_dotenv()` from python-dotenv before building the router."
)
"""Appended to a configuration error naming a variable that the nearest ``.env`` sets and the environment lacks."""
