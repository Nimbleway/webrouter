"""Question sets: SimpleQA, SimpleQA Verified, FRAMES, SealQA, two unlabeled sets of Nimble's, or a CSV of yours.

Sampling mirrors ``SimpleQAEval`` in openai/simple-evals -- ``random.Random(seed).sample(rows, limit)``
on the stdlib RNG -- so ``limit=500`` with the default seed is the same 500 questions every published
SimpleQA figure was measured on, and a slice is reproducible on any machine.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import random
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field

from web_router.constants import (
    EXTRA_EVALS,
    MSG_EVALS_BAD_LIMIT,
    MSG_EVALS_BLANK_VALUE,
    MSG_EVALS_CSV_NEEDS_PATH,
    MSG_EVALS_MISSING_COLUMN,
    MSG_EVALS_NEEDS_PYARROW,
    MSG_EVALS_UNKNOWN_SET,
    MSG_EVALS_UNKNOWN_SPLIT,
    SIMPLEQA_SEED,
)
from web_router.constants_evals import (
    EVALS_CSV_SET,
    EVALS_QUESTION_SETS,
    EVALS_URL_DECODE_PASSES,
    FRAMES_ANSWER_COLUMN,
    FRAMES_METADATA_COLUMNS,
    FRAMES_NAME,
    FRAMES_QUESTION_COLUMN,
    FRAMES_URL,
    MSG_EVALS_UNKNOWN_PUBLIC_SET,
    PUBLIC_BENCHMARKS_ANSWER_COLUMN,
    PUBLIC_BENCHMARKS_COMMIT,
    PUBLIC_BENCHMARKS_QUESTION_COLUMN,
    PUBLIC_BENCHMARKS_SETS,
    PUBLIC_BENCHMARKS_URL,
    SEALQA_METADATA_COLUMNS,
    SEALQA_SPLITS,
    SEALQA_URL,
    SEALQA_WITHHELD_MARKERS,
    SIMPLEQA_ANSWER_COLUMN,
    SIMPLEQA_DOWNLOAD_TIMEOUT,
    SIMPLEQA_NAME,
    SIMPLEQA_QUESTION_COLUMN,
    SIMPLEQA_URL,
    SIMPLEQA_VERIFIED_NAME,
    SIMPLEQA_VERIFIED_URL,
)
from web_router.exceptions import ConfigurationError


class Question(BaseModel):
    """One question and the answer the dataset holds as gold, or none in an unlabeled set."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    answer: str | None = Field(default=None, min_length=1, description="The gold answer; None in an unlabeled set.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Whatever else the row carried.")


class Dataset(BaseModel):
    """A named list of :class:`Question`, in the order the source gave them.

    Loop over :attr:`questions`, not the dataset: a pydantic model iterates as
    its ``(field, value)`` pairs, so ``for q in dataset`` hands back tuples.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    questions: list[Question] = Field(default_factory=list)
    withheld_markers: tuple[str, ...] = Field(
        default=(),
        description=(
            "Text that marks a URL as one of this set's own pages, which may hold its answers: a result whose URL, "
            "percent-decoded and in any case, contains one is never judged."
        ),
    )

    def __len__(self) -> int:
        return len(self.questions)

    def withholds(self, url: str) -> bool:
        """Whether a result from ``url`` might be this set's own answer key, and so is kept from the judge."""
        if not self.withheld_markers:
            return False
        try:
            urlsplit(url)
        except ValueError:
            # A URL no parser can read cannot be shown not to be one of the set's pages, so it is not judged.
            return True
        # Decoded until it stops changing, so neither `Se%61lQA` nor a twice-encoded copy hides the name.
        text = url
        for _ in range(EVALS_URL_DECODE_PASSES):
            decoded = unquote(text)
            if decoded == text:
                break
            text = decoded
        else:
            if unquote(text) != text:
                return True  # still encoded past the bound: withheld, not decoded further
        text = text.lower()
        return any(marker.lower() in text for marker in self.withheld_markers)

    def sample(self, limit: int | None, seed: int = SIMPLEQA_SEED) -> Dataset:
        """The first ``limit`` of a seeded shuffle -- the canonical slice when the seed is the default.

        ``limit`` at or above the size, or ``None``, is the whole set in its
        own order, as upstream returns it.
        """
        if limit is None or limit >= len(self.questions):
            return self
        if limit < 1:
            raise ValueError(MSG_EVALS_BAD_LIMIT.format(limit=limit))
        sampled = random.Random(seed).sample(self.questions, limit)
        return self.model_copy(update={"questions": sampled})

    @classmethod
    def from_rows(
        cls,
        name: str,
        rows: list[dict[str, Any]],
        *,
        question_column: str = SIMPLEQA_QUESTION_COLUMN,
        answer_column: str | None = SIMPLEQA_ANSWER_COLUMN,
        source: str = "the rows",
    ) -> Dataset:
        """Build from dict rows, keeping every column but the two named under ``metadata``.

        ``answer_column=None`` reads an unlabeled set; a named one must hold an answer on every row.
        """
        named = (question_column,) if answer_column is None else (question_column, answer_column)
        questions: list[Question] = []
        for index, row in enumerate(rows, start=1):
            for column in named:
                if column not in row:
                    raise ValueError(MSG_EVALS_MISSING_COLUMN.format(path=source, column=column, columns=sorted(row)))
                # A short CSV row reads as None, which str() below would turn
                # into the gold answer "None"; a blank cell is as meaningless.
                if row[column] is None or not str(row[column]).strip():
                    raise ValueError(MSG_EVALS_BLANK_VALUE.format(path=source, row=index, column=column))
            metadata = {key: _parse_cell(value) for key, value in row.items() if key not in named}
            answer = None if answer_column is None else str(row[answer_column])
            questions.append(Question(question=str(row[question_column]), answer=answer, metadata=metadata))
        return cls(name=name, questions=questions)

    @classmethod
    def from_csv(
        cls,
        path: str | Path,
        *,
        question_column: str = SIMPLEQA_QUESTION_COLUMN,
        answer_column: str | None = SIMPLEQA_ANSWER_COLUMN,
        name: str | None = None,
        limit: int | None = None,
        seed: int = SIMPLEQA_SEED,
    ) -> Dataset:
        """Read a CSV with a header: one question per row, the two named columns holding question and gold, or
        ``answer_column=None`` for an unlabeled set."""
        path = Path(path)
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        dataset = cls.from_rows(
            name or path.stem, rows, question_column=question_column, answer_column=answer_column, source=str(path)
        )
        return dataset.sample(limit, seed)

    @classmethod
    def for_set(
        cls,
        name: str,
        *,
        path: str | Path | None = None,
        question_column: str = SIMPLEQA_QUESTION_COLUMN,
        answer_column: str | None = SIMPLEQA_ANSWER_COLUMN,
        limit: int | None = None,
        seed: int = SIMPLEQA_SEED,
    ) -> Dataset:
        """A question set by name -- ``simpleqa``, ``simpleqa-verified``, ``frames``, ``sealqa-0``, ``sealqa-hard``,
        the unlabeled ``due-diligence`` and ``lead-enrichment``, or ``csv``, your own file at ``path`` read by the
        two named columns.

        For a named set, ``path`` is a local copy of its file in place of the download.

        Raises:
            ValueError: An unknown name, or ``csv`` with no ``path``.
        """
        if name not in EVALS_QUESTION_SETS:
            raise ValueError(MSG_EVALS_UNKNOWN_SET.format(name=name, sets=", ".join(EVALS_QUESTION_SETS)))
        method, leading = EVALS_QUESTION_SETS[name]
        # Looked up on the class at call time, so a subclass's own loader is the one used.
        loader = getattr(cls, method)
        if name != EVALS_CSV_SET:
            return loader(*leading, path=path, limit=limit, seed=seed)  # type: ignore[no-any-return]
        if path is None:
            raise ValueError(MSG_EVALS_CSV_NEEDS_PATH.format(name=name))
        return loader(  # type: ignore[no-any-return]
            path, question_column=question_column, answer_column=answer_column, limit=limit, seed=seed
        )

    @staticmethod
    def csv_identity(path: str | Path, question_column: str, answer_column: str) -> dict[str, str]:
        """What tells one CSV's questions from another's: the file's SHA-256 and the two columns read.

        For a run's record, so two runs combine only when they asked the same questions from the same file.
        """
        return {
            "dataset_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            "question_column": question_column,
            "answer_column": answer_column,
        }

    @classmethod
    def simpleqa(
        cls,
        *,
        path: str | Path | None = None,
        limit: int | None = None,
        seed: int = SIMPLEQA_SEED,
    ) -> Dataset:
        """The SimpleQA test set from openai/simple-evals, downloaded unless a local copy is given.

        Every ``metadata`` carries the upstream ``topic``, ``answer_type`` and
        gold ``urls``.
        """
        if path is not None:
            dataset = cls.from_csv(path, name=SIMPLEQA_NAME)
            return dataset.sample(limit, seed)
        rows = list(csv.DictReader(io.StringIO(_fetch(SIMPLEQA_URL, follow_redirects=False).decode("utf-8"))))
        dataset = cls.from_rows(SIMPLEQA_NAME, rows, source=SIMPLEQA_URL)
        return dataset.sample(limit, seed)

    @classmethod
    def simpleqa_verified(
        cls,
        *,
        path: str | Path | None = None,
        limit: int | None = None,
        seed: int = SIMPLEQA_SEED,
    ) -> Dataset:
        """Google DeepMind's SimpleQA Verified, downloaded at a pinned commit unless a local copy is given.

        SimpleQA's columns, cleaned and re-checked: every ``metadata`` carries
        the upstream ``topic``, ``answer_type``, ``multi_step``,
        ``requires_reasoning`` and gold ``urls``.
        """
        if path is not None:
            return cls.from_csv(path, name=SIMPLEQA_VERIFIED_NAME).sample(limit, seed)
        rows = list(csv.DictReader(io.StringIO(_fetch(SIMPLEQA_VERIFIED_URL).decode("utf-8"))))
        return cls.from_rows(SIMPLEQA_VERIFIED_NAME, rows, source=SIMPLEQA_VERIFIED_URL).sample(limit, seed)

    @classmethod
    def frames(
        cls,
        *,
        path: str | Path | None = None,
        limit: int | None = None,
        seed: int = SIMPLEQA_SEED,
    ) -> Dataset:
        """Google's FRAMES test set, downloaded unless a local copy of its ``test.tsv`` is given.

        Multi-hop questions -- each answer is put together from two to fifteen
        Wikipedia articles -- so a single search rarely settles one, which is
        what makes it a test of an agent rather than of one retrieval. Every
        ``metadata`` carries the upstream ``reasoning_types`` and the gold
        ``wiki_links``.
        """
        if path is not None:
            text = Path(path).read_text(encoding="utf-8")
            source = str(path)
        else:
            text = _fetch(FRAMES_URL).decode("utf-8")
            source = FRAMES_URL
        kept = (FRAMES_QUESTION_COLUMN, FRAMES_ANSWER_COLUMN, *FRAMES_METADATA_COLUMNS)
        rows = [
            {key: value for key, value in row.items() if key in kept}
            for row in csv.DictReader(io.StringIO(text), delimiter="\t")
        ]
        dataset = cls.from_rows(
            FRAMES_NAME,
            rows,
            question_column=FRAMES_QUESTION_COLUMN,
            answer_column=FRAMES_ANSWER_COLUMN,
            source=source,
        )
        return dataset.sample(limit, seed)

    @classmethod
    def sealqa(
        cls,
        split: str = "seal-0",
        *,
        path: str | Path | None = None,
        limit: int | None = None,
        seed: int = SIMPLEQA_SEED,
    ) -> Dataset:
        """SealQA's ``seal-0`` or ``seal-hard`` split, downloaded at a pinned commit unless a local Parquet is given.

        Questions built so that search turns up conflicting, noisy or unhelpful
        pages, where frontier systems still score low. Every ``metadata``
        carries the upstream ``freshness``, ``question_types``,
        ``effective_year``, ``search_results`` (how the web answers it),
        ``topic`` and gold ``urls``. The set withholds results from the
        dataset's own pages and copies (:meth:`withholds`), so a backend is not
        scored on finding the answer key. Reading Parquet needs ``pyarrow``,
        which the ``evals`` extra installs.
        """
        if split not in SEALQA_SPLITS:
            raise ValueError(MSG_EVALS_UNKNOWN_SPLIT.format(split=split, splits=sorted(SEALQA_SPLITS)))
        try:
            import pyarrow.parquet as parquet
        except ImportError as exc:
            raise ConfigurationError(MSG_EVALS_NEEDS_PYARROW.format(extra=EXTRA_EVALS)) from exc
        if path is not None:
            data: Any = str(path)
            source = str(path)
        else:
            source = SEALQA_URL.format(file=SEALQA_SPLITS[split])
            data = io.BytesIO(_fetch(source))
        kept = ("question", "answer", *SEALQA_METADATA_COLUMNS)
        rows = [{key: row[key] for key in kept if key in row} for row in parquet.read_table(data).to_pylist()]
        dataset = cls.from_rows(
            f"sealqa-{split}", rows, question_column="question", answer_column="answer", source=source
        ).model_copy(update={"withheld_markers": SEALQA_WITHHELD_MARKERS})
        return dataset.sample(limit, seed)

    @classmethod
    def public_benchmarks(
        cls,
        name: str,
        *,
        path: str | Path | None = None,
        limit: int | None = None,
        seed: int = SIMPLEQA_SEED,
    ) -> Dataset:
        """A set from Nimbleway/Public-benchmarks, read at a pinned commit unless a local copy is given.

        ``due-diligence`` and ``lead-enrichment``: what people search about a company to invest in or sell to. Only
        ``question`` is read, and ``answer`` when present. With no answers, ``evaluate()`` refuses them.
        """
        if name not in PUBLIC_BENCHMARKS_SETS:
            raise ValueError(MSG_EVALS_UNKNOWN_PUBLIC_SET.format(name=name, sets=sorted(PUBLIC_BENCHMARKS_SETS)))
        if path is not None:
            text = Path(path).read_text(encoding="utf-8")
            source = str(path)
        else:
            source = PUBLIC_BENCHMARKS_URL.format(commit=PUBLIC_BENCHMARKS_COMMIT, folder=PUBLIC_BENCHMARKS_SETS[name])
            text = _fetch(source).decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        answer = (
            PUBLIC_BENCHMARKS_ANSWER_COLUMN if PUBLIC_BENCHMARKS_ANSWER_COLUMN in (reader.fieldnames or ()) else None
        )
        rows = [{key: row[key] for key in (PUBLIC_BENCHMARKS_QUESTION_COLUMN, answer) if key} for row in reader]
        return cls.from_rows(
            name, rows, question_column=PUBLIC_BENCHMARKS_QUESTION_COLUMN, answer_column=answer, source=source
        ).sample(limit, seed)


def _fetch(url: str, *, follow_redirects: bool = True) -> bytes:
    """A question set's file, downloaded.

    Following a redirect is safe here, unlike on a provider: Hugging Face
    answers with a hop to its CDN, and this request carries no key.
    """
    # Imported here: httpx arrives with any method's extra, and a local copy needs none.
    import httpx

    response = httpx.get(url, timeout=SIMPLEQA_DOWNLOAD_TIMEOUT, follow_redirects=follow_redirects)
    response.raise_for_status()
    return response.content


def _parse_cell(value: Any) -> Any:
    """A cell that holds a JSON or Python literal -- SimpleQA's ``metadata`` -- as the object it spells."""
    if not isinstance(value, str) or not value.startswith(("{", "[")):
        return value
    try:
        return json.loads(value)
    except ValueError:
        pass
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value


__all__ = ["Dataset", "Question"]
