"""A trained classifier as a router: a scikit-learn pipeline, saved and loaded with joblib.

The router asks the model for each method's probability with `predict_proba`
and ranks the methods it offers, best first; the backends stay in `auto`'s
order. It runs in-process in about a millisecond, so it declares
`timeout_s = 0.05`, the library's default, written out so it is easy to find.

The training set below is a toy, there so this runs with no data of your own.
Train on your own prompts, labelled with the method that served each one best
-- route-eval's frozen sets are one source -- and load the file you saved.

scikit-learn is not a dependency of web-router. Runs offline with no key:

    uv run --with scikit-learn python examples/router_classifier.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from web_router import RouteDecision, RouteRequest, WebRouter

TRAINING = (
    ("who won the 2022 world cup", "answer"),
    ("what year did the berlin wall fall", "answer"),
    ("capital of australia", "answer"),
    ("best laptops for students", "search"),
    ("python asyncio tutorial", "search"),
    ("latest news on electric cars", "search"),
    ("summarize https://example.com/post", "extract"),
    ("get the text of https://example.com/page", "extract"),
    ("compare three cloud vendors on price and support, as a report", "research"),
    ("analyze the market for home batteries in europe", "research"),
)

PROMPTS = ("who wrote the origin of species", "summarize https://example.com/about", "python web frameworks")


class ClassifierRouter:
    """Ranks the offered methods by a fitted model's `predict_proba`, most likely first."""

    timeout_s = 0.05

    def __init__(self, model: Any) -> None:
        self.model = model  # anything with predict_proba() and classes_, a pipeline that vectorizes text

    def route(self, req: RouteRequest) -> RouteDecision:
        offered = {candidate.tool for candidate in req.candidates}
        scores = dict(zip(map(str, self.model.classes_), self.model.predict_proba([req.prompt])[0], strict=True))
        tools = sorted((tool for tool in scores if tool in offered), key=scores.__getitem__, reverse=True)
        if not tools:
            return RouteDecision()
        best = float(scores[tools[0]])
        return RouteDecision(tools=tools, confidence=best, reason=f"classifier: {tools[0]} at p={best:.2f}")


def train(path: Path) -> None:
    """Fit a tiny text classifier on TRAINING and save it to ``path``."""
    import joblib
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline

    pipeline = make_pipeline(TfidfVectorizer(ngram_range=(1, 2)), LogisticRegression(C=10.0, max_iter=1000))
    pipeline.fit([prompt for prompt, _ in TRAINING], [tool for _, tool in TRAINING])
    joblib.dump(pipeline, path)


def main() -> None:
    try:
        import joblib
    except ImportError:
        print("This example needs scikit-learn: uv run --with scikit-learn python examples/router_classifier.py")
        return
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "router.joblib"
        train(path)
        router = ClassifierRouter(joblib.load(path))
    with WebRouter(router=router) as client:
        for prompt in PROMPTS:
            plan = client.plan(prompt)
            print(f"{prompt!r}\n  -> {plan.tool}  ({plan.decision.reason})")


if __name__ == "__main__":
    main()
