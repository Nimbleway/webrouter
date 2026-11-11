"""route-eval's dollar and unknown formatting, shared by both reports.

:mod:`route_eval_report` imports :mod:`route_eval_report_matrix`, so the two
helpers live here rather than in either. Imported as a sibling, by name, as
``_common`` is.
"""

from __future__ import annotations


def _usd(value: float | None, unknown: int = 0) -> str:
    if value is None:
        return f"unknown ({unknown})" if unknown else "unknown"
    return _unknown(f"${value:.4f}", unknown)


def _unknown(text: str, unknown: int) -> str:
    return text + (f" ({unknown} unknown)" if unknown else "")
