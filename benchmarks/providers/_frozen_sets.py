"""Each frozen set and the scan folders it is made of.

Kept apart from ``frozen.py``, which re-exports ``FROZEN_SETS`` and reads it
from its own name, so a test can swap it there. Imported as a sibling, by
name, as ``_common`` is.
"""

from __future__ import annotations

from typing import Any

from _frozen_mixed import MIXED
from _tools_effort_plan import QUESTIONS as MIXED_QUESTIONS

FROZEN_SETS: dict[str, dict[str, Any]] = {
    "sealqa-hard-254": {"questions": "sealqa-hard", "method": "search", "runs": ["sealqa-hard-254"]},
    "sealqa-hard-254-twin": {"questions": "sealqa-hard", "method": "search", "runs": ["sealqa-hard-254-twin"]},
    # Vendor modes beside the same day's defaults. Apart from sealqa-hard-254: these scans withheld the set's own pages.
    "sealqa-hard-254-modes": {
        "questions": "sealqa-hard",
        "method": "search",
        "runs": ["sealqa-hard-254-defaults-1116", "sealqa-hard-254-modes"],
    },
    "simpleqa-300-v2": {
        "questions": "simpleqa",
        "method": "search",
        "runs": ["simpleqa-300-v2", "simpleqa-300-keenable", "simpleqa-300-deepseek"],
    },
    "answer-simpleqa-300": {"questions": "simpleqa", "method": "answer", "runs": ["answer-simpleqa-300"]},
    # After answer-simpleqa-300, whose rows it reuses for its easy questions.
    "tools-effort-60": {"questions": MIXED_QUESTIONS, "method": MIXED, "runs": ["tools-effort-60"]},
}
"""Each frozen set: the question set its scans sampled, the method they called, and the scan folders it is made of.

A set made of several scans is one scan with backends added later, as
``run.py --combine`` merges them: the scans must share what ``--combine``
requires for their method (``COMBINE_KEYS``), the same questions and the same
gold answers; a key a scan predates reads as missing.
"""
