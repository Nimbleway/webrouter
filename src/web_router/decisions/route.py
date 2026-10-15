"""Labels in, a route out: pure, so a measurement can replay it on labels a person gave."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from web_router.constants import (
    ROUTE_ADVICE_DECIDER_ERROR,
    ROUTE_ADVICE_LEANED,
    ROUTE_ADVICE_LOW_CONFIDENCE,
    ROUTE_ADVICE_NO_BACKEND,
    ROUTE_ADVICE_NO_URL,
    ROUTE_CONFIDENCE,
    ROUTE_DEFAULT_LABELS,
    ROUTE_FALLBACK_DECIDER_ERROR,
    ROUTE_FALLBACK_LOW_CONFIDENCE,
    ROUTE_FALLBACK_NO_BACKEND,
    ROUTE_FALLBACK_NO_URL,
    ROUTE_LABEL_LINE,
    ROUTE_LABEL_LINES,
    ROUTE_METHODS,
    ROUTE_MIN_CONFIDENCE,
    ROUTE_TOOL_LINE,
    ROUTE_UNSURE,
    URL_TRAILING_PUNCTUATION,
)

_URL = re.compile(r"https?://[^\s<>\"')\]]+")

ChainEntry = tuple[str, dict[str, Any]]


@dataclass(frozen=True)
class Answer:
    """One choice answer: the pick, how sure the model is, and the whole distribution."""

    choice: str
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)


@dataclass
class Route:
    """What to run, decided before anything is spent: everything :meth:`~web_router.WebRouter.execute` needs."""

    query: str
    method: str
    chain: tuple[ChainEntry, ...]
    urls: list[str] = field(default_factory=list)
    output_schema: Any = None
    labels: dict[str, Answer] = field(default_factory=dict)
    fallback: tuple[str, ...] = ()
    """Why the route is not the plain reading of the labels, if it is not: no silent downgrade."""
    requested: str | None = None
    """The method asked for before any downgrade: the tool label, or research for a schema, extract for a URL."""
    latency_ms: float = 0.0

    @property
    def providers(self) -> list[str]:
        return [name for name, _ in self.chain]

    @property
    def advice(self) -> str:
        """One coherent recommendation, for an agent to act on: never two tools that contradict each other."""
        asked = self.labels.get("tool")
        wanted = self.requested or (asked.choice if asked is not None else self.method)
        if ROUTE_FALLBACK_NO_URL in self.fallback:
            return ROUTE_ADVICE_NO_URL
        if ROUTE_FALLBACK_NO_BACKEND in self.fallback and wanted != self.method:
            return ROUTE_ADVICE_NO_BACKEND.format(method=self.method, wanted=wanted)
        # A tool label that is missing or below the threshold took its default: say so, and where it leaned.
        if ROUTE_FALLBACK_LOW_CONFIDENCE in self.fallback and (
            asked is None or asked.confidence < ROUTE_MIN_CONFIDENCE
        ):
            template = ROUTE_ADVICE_LOW_CONFIDENCE if wanted == self.method else ROUTE_ADVICE_LEANED
            return template.format(method=self.method, wanted=wanted)
        if ROUTE_FALLBACK_DECIDER_ERROR in self.fallback:
            return ROUTE_ADVICE_DECIDER_ERROR.format(method=self.method)
        return self.method

    def __str__(self) -> str:
        """What an agent reads: each label as a plain instruction, with its confidence."""
        tool = self.labels.get("tool")
        confidence = ROUTE_CONFIDENCE.format(value=tool.confidence) if tool is not None else ""
        lines = [ROUTE_TOOL_LINE.format(advice=self.advice, confidence=confidence)]
        for name, heading, texts in ROUTE_LABEL_LINES:
            answer = self.labels.get(name)
            if answer is None:
                continue
            if answer.confidence < ROUTE_MIN_CONFIDENCE:
                # The route used the default for this label, so the agent reads the default, marked unsure.
                default = ROUTE_DEFAULT_LABELS[name]
                text, conf = texts.get(default, default), ROUTE_UNSURE.format(value=answer.confidence)
            else:
                text, conf = texts.get(answer.choice, answer.choice), ROUTE_CONFIDENCE.format(value=answer.confidence)
            lines.append(ROUTE_LABEL_LINE.format(heading=heading, text=text, confidence=conf))
        return "\n".join(lines)


def urls_in(text: str) -> list[str]:
    """The URLs in ``text``, without the punctuation that ends a sentence around them."""
    return [url.rstrip(URL_TRAILING_PUNCTUATION) for url in _URL.findall(text)]


def label_values(
    labels: Mapping[str, Answer], min_confidence: float = ROUTE_MIN_CONFIDENCE
) -> tuple[dict[str, str], bool]:
    """Each label's value, its default where the model was unsure; and whether any default was used."""
    values = dict(ROUTE_DEFAULT_LABELS)
    unsure = False
    for name in values:
        answer = labels.get(name)
        if answer is not None and answer.confidence >= min_confidence:
            values[name] = answer.choice
        else:
            unsure = True
    return values, unsure


def route_from_labels(
    query: str,
    values: Mapping[str, str],
    chains: Mapping[str, Sequence[ChainEntry]],
    *,
    urls: Sequence[str] = (),
    output_schema: Any = None,
) -> tuple[str, tuple[ChainEntry, ...], str | None]:
    """The method, its chain, and the fallback reason, from label values and this environment's default chains.

    ``chains`` maps each method to the default auto chain here, so a method
    with none -- research or browse with no key for it -- downgrades to search.
    """
    fallback: str | None = None
    if output_schema is not None:
        method = "research"
    else:
        method = values["tool"]
        if method == "extract" and not urls:
            method, fallback = "search", ROUTE_FALLBACK_NO_URL
    if method not in ROUTE_METHODS or not chains.get(method):
        method, fallback = "search", fallback or ROUTE_FALLBACK_NO_BACKEND
    return method, tuple(chains[method]), fallback
