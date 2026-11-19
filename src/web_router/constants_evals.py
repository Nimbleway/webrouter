"""Choosing a judge by name, and the scorer's messages: kept apart so ``constants.py`` stops growing."""

from web_router.constants import EVALS_JUDGE_MODEL, EVALS_JUDGE_NAME

EVALS_DEFAULT_JUDGE_SPEC = f"{EVALS_JUDGE_NAME}:{EVALS_JUDGE_MODEL}"
"""The judge a benchmark uses unless ``--judge`` names another: the same text its report records."""

EVALS_JUDGE_SPEC_HELP = (
    "who synthesizes and grades: openai[:<model>], anthropic[:<model>], litellm:<model> for any model LiteLLM "
    "reaches, or package.module:Class for a Judge of your own (default: {default}); scores compare only with "
    "runs of the same judge"
)

EVALS_LITELLM_JUDGE_NAME = "litellm"
"""How ``LiteLLMJudge`` names itself in errors and in a report's ``judge`` field."""

EXTRA_EVALS_LITELLM = "evals-litellm"

EVALS_LITELLM_STOPPED_SHORT: frozenset[str] = frozenset({"length", "content_filter"})
"""The finish reasons, in LiteLLM's OpenAI-shaped reply, of a reply cut off before it held an answer to read."""

EVALS_LITELLM_IMPORT_ENV = {"LITELLM_MODE": "PRODUCTION", "LITELLM_LOCAL_MODEL_COST_MAP": "True"}
"""Set before LiteLLM is imported, unless the caller set them: without them its import reads ``.env`` and fetches
its price list over the network."""

MSG_JUDGE_SPEC = (
    "{spec!r} names no judge: use openai[:<model>], anthropic[:<model>], litellm:<model> or package.module:Class"
)
MSG_JUDGE_NEEDS_MODEL = "{spec!r} names no model: LiteLLM reaches many, so name one, as in litellm:<model>"
MSG_JUDGE_IMPORT = "Could not load the judge {spec!r}: {error}"
MSG_JUDGE_NOT_A_JUDGE = "{spec!r} is not a Judge subclass; a judge of your own subclasses web_router.evals.Judge"

MSG_SCORE_OUT_OF_SCALE = "{name} is {value}, outside the scorecard's scale {low}-{high}"
MSG_SCORE_UNKNOWN_REASON = "A reason is given for {names}, which the scorecard does not score"
MSG_SCORE_BAD_SCALE = "A scorecard's scale is a finite lowest and highest score, lowest first, not {scale}"
