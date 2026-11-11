"""The question sets' sources, names and columns, and the messages their loaders raise.

Kept apart from :mod:`web_router.constants`, so that file stops growing.
"""

from __future__ import annotations

SIMPLEQA_URL = "https://openaipublic.blob.core.windows.net/simple-evals/simple_qa_test_set.csv"
"""Where openai/simple-evals reads the SimpleQA test set from."""

SIMPLEQA_NAME = "simpleqa"

SIMPLEQA_QUESTION_COLUMN = "problem"

SIMPLEQA_ANSWER_COLUMN = "answer"

SIMPLEQA_DOWNLOAD_TIMEOUT = 60.0

EVALS_CSV_SET = "csv"
EVALS_QUESTION_SETS: dict[str, tuple[str, tuple[str, ...]]] = {
    "simpleqa": ("simpleqa", ()),
    "simpleqa-verified": ("simpleqa_verified", ()),
    "frames": ("frames", ()),
    "sealqa-0": ("sealqa", ("seal-0",)),
    "sealqa-hard": ("sealqa", ("seal-hard",)),
    "due-diligence": ("public_benchmarks", ("due-diligence",)),
    "lead-enrichment": ("public_benchmarks", ("lead-enrichment",)),
    EVALS_CSV_SET: ("from_csv", ()),
}
"""Each question set by the name a caller picks it with: the :class:`~web_router.evals.Dataset` loader that reads
it, and that loader's leading arguments. ``csv`` is the caller's own file."""

SIMPLEQA_VERIFIED_URL = (
    "https://huggingface.co/datasets/google/simpleqa-verified/resolve/"
    "0dc97e0d28d8233463e005cdc4475cc2a13ba2dc/simpleqa_verified.csv"
)
"""Google DeepMind's SimpleQA Verified (2025): 1,000 single-fact questions, pinned to one commit of the dataset.

Pinned rather than read from ``main`` so every run reads the same questions and gold answers; moving the pin is
a deliberate change. Questions frontier models get wrong without search, which is what makes it the set a
no-search baseline is measured on.
"""

SIMPLEQA_VERIFIED_NAME = "simpleqa-verified"

FRAMES_URL = "https://huggingface.co/datasets/google/frames-benchmark/resolve/main/test.tsv"
"""Google's FRAMES test set: multi-hop questions whose answers take several documents to put together."""

FRAMES_NAME = "frames"

FRAMES_QUESTION_COLUMN = "Prompt"

FRAMES_ANSWER_COLUMN = "Answer"

FRAMES_METADATA_COLUMNS: tuple[str, ...] = ("reasoning_types", "wiki_links")
"""The upstream columns a FRAMES question keeps in its metadata; the numbered link columns repeat ``wiki_links``."""

SEALQA_URL = "https://huggingface.co/datasets/vtllms/sealqa/resolve/0c298f519c871579c75920189b3858da29fe2c81/{file}"
"""SealQA (2025): questions built so that search returns conflicting, noisy or unhelpful results, at a pinned commit.

Pinned because SealQA refreshes its answers on a schedule; moving the pin is a deliberate change. Published as
Parquet, so reading it needs ``pyarrow`` (the ``evals`` extra).
"""

SEALQA_SPLITS: dict[str, str] = {"seal-0": "seal-0.parquet", "seal-hard": "seal-hard.parquet"}
"""Split -> file. Seal-0 (111 questions) is the subset frontier models answer worst; Seal-Hard (254) contains it."""

SEALQA_METADATA_COLUMNS: tuple[str, ...] = (
    "urls",
    "freshness",
    "question_types",
    "effective_year",
    "search_results",
    "topic",
)
"""The upstream columns a SealQA question keeps in its metadata. ``canary`` is dropped: it exists to mark copies."""

EVALS_URL_DECODE_PASSES = 4
"""How many times a result URL is percent-decoded before it is matched against a set's withheld markers.

Enough for any real double or triple encoding; a URL still changing after this many passes is withheld rather than
decoded further, so a pathological one costs a bounded amount of work.
"""

SEALQA_WITHHELD_MARKERS: tuple[str, ...] = (
    "sealqa",
    "2506.01062",
    "1fJunutgxNaiU6-Met_wzyEdG-tXQLfrM",
    "19SGV6dJkaqF9ziaoIMnx9PJwih8nDwzp",
)
"""What marks a URL as one of SealQA's own pages, which may carry its questions and answers.

Its name, which the dataset's path and its copies carry; its paper's arXiv id, on arXiv or any host that mirrors
it by id (alphaXiv, Hugging Face's paper page, Semantic Scholar); and the ids of the grading notebook and the folder
of older releases its dataset card links. Only these are withheld, so an unrelated paper on arXiv or dataset on
Hugging Face still reaches the judge -- checked against the card at the pinned commit on 2026-10-08.
"""

PUBLIC_BENCHMARKS_COMMIT = "PENDING-PUBLIC-BENCHMARKS-MERGE"
"""The commit of Nimbleway/Public-benchmarks the unlabeled sets are read at.

A placeholder until the pull request adding the sets there is merged; it becomes that merge commit before a release,
and moving it afterwards is a deliberate change, as on the other pinned sets.
"""

PUBLIC_BENCHMARKS_URL = (
    "https://raw.githubusercontent.com/Nimbleway/Public-benchmarks/{commit}/{folder}/data/questions.csv"
)
"""Where a set published in Nimbleway/Public-benchmarks is read from: its folder's ``data/questions.csv``."""

PUBLIC_BENCHMARKS_SETS: dict[str, str] = {
    "due-diligence": "Due-Diligence-Benchmark",
    "lead-enrichment": "Lead-Enrichment-Benchmark",
}
"""Each unlabeled set published in Nimbleway/Public-benchmarks, by name -> its folder there.

``due-diligence``: questions people search for when researching a company or fund to invest in.
``lead-enrichment``: questions people search for about a company they want to sell to. Neither carries answers.
"""

PUBLIC_BENCHMARKS_QUESTION_COLUMN = "question"

PUBLIC_BENCHMARKS_ANSWER_COLUMN = "answer"
"""Read as gold when a set has it; every other column (``category`` and the like) is ignored."""

MSG_EVALS_UNKNOWN_PUBLIC_SET = "Public-benchmarks has no question set {name!r}; choose one of {sets}."

MSG_EVALS_UNANSWERED = (
    "evaluate() grades each answer against the dataset's gold, and {name!r} has {count} question(s) with none "
    "(the first: {first!r}); score an unlabeled set with a judge of your own over benchmark()'s rows"
)
