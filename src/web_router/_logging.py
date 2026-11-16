"""The package logger: only a ``NullHandler``, so nothing prints until the application configures logging.

Every module logs under ``web_router``; ``logging.basicConfig(level=logging.INFO)`` shows each ``auto`` hop,
cooldown and refused key. See ``docs/production.mdx``.
"""

from __future__ import annotations

import logging

from web_router.constants_production import LOGGER_NAME


def install_null_handler() -> None:
    """Give the package logger its ``NullHandler``, once."""
    logger = logging.getLogger(LOGGER_NAME)
    if not any(isinstance(handler, logging.NullHandler) for handler in logger.handlers):
        logger.addHandler(logging.NullHandler())


install_null_handler()
