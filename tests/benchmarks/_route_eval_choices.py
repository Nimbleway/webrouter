"""The choices route-eval's matrix and comparison tests share: a builder, and a router and oracle on four questions."""

from __future__ import annotations


def _c(
    qid: str, tool: str, correct: bool, cost: float | None, latency: float | None, path: str = "", right: tuple = ()
) -> dict:
    """One choice, as :func:`route_eval_matrix.choices` writes it; ``right`` the question's right tools."""
    return {
        "question_id": qid,
        "path": path or f"{tool}:x",
        "tool": tool,
        "right_tools": list(right),
        "correct": correct,
        "cost_usd": cost,
        "latency_s": latency,
    }


RIGHT = {"q1": ("answer", "research"), "q2": ("search",), "q3": (), "q4": ("research",)}
ORACLE = [
    _c("q1", "answer", True, 0.005, 2.0, right=RIGHT["q1"]),
    _c("q2", "search", True, 0.001, 5.0, right=RIGHT["q2"]),
    _c("q3", "search", False, 0.001, 5.0),  # no path right: the oracle walked the static order
    _c("q4", "research", True, 0.5, 60.0, right=RIGHT["q4"]),
]
ROUTER = [
    _c("q1", "research", True, 0.5, 80.0, right=RIGHT["q1"]),  # right, but a research bill where an answer did
    _c("q2", "search", True, 0.002, 4.0, right=RIGHT["q2"]),  # the best tool, another backend
    _c("q3", "research", False, 1.0, None),  # nobody was right; its time unrecorded
    _c("q4", "search", False, 0.001, 3.0, right=RIGHT["q4"]),  # cheap and wrong
]
