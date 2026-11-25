"""The local browser's profile cleanup timings.

Kept apart from :mod:`web_router.constants`, so that file stops growing.
"""

from __future__ import annotations

LOCAL_PROFILE_REMOVE_RETRIES = 10
"""How many more times a profile is removed after the first try finds the folder not yet empty."""

LOCAL_PROFILE_REMOVE_DELAY = 0.1
"""Seconds between those tries.

Chrome's helper processes -- the crash reporter, the GPU and network services -- can outlive the main one by a
moment and write into the profile while it is being removed, which fails with "Directory not empty". Ten tries a
tenth of a second apart give them a second to finish before the profile is reported left behind.
"""
