"""Deciders and the built-in ``llm`` and ``systemone`` routers: presets, limits and messages.

Experimental: :mod:`web_router.routing` is new and may change before 1.0.
Kept apart from :mod:`web_router.constants`, so that file stops growing.
"""

from __future__ import annotations

LLM_ROUTER_NAME = "llm"
"""The registered name of the built-in router on any OpenAI-compatible chat-completions API."""

SYSTEMONE_ROUTER_NAME = "systemone"
"""The registered name of the built-in router on a System One decision API."""

DECIDER_INSTRUCTIONS = (
    "Which method and backend should serve this web request? search returns links and snippets in a second; "
    "extract reads the URLs given; answer writes one answer with sources in seconds; research runs a "
    "multi-minute agentic job that bills per run; browse hands an interactive task to a browser agent."
)
"""What every decider is told before its options."""

DECIDER_PINNED = " The method is fixed to {tool}: rank its backends only."
"""Added to the instructions when the caller pinned the method."""

DECIDER_OPTION = "{tool} on {provider}, {cost}, {latency}"
DECIDER_COST = "${cost:g}"
DECIDER_COST_UNKNOWN = "unknown cost"
DECIDER_LATENCY = "p90 {latency:g}s"
DECIDER_LATENCY_UNKNOWN = "unknown latency"

DECIDER_REASON = "{name}: {top}"
DECIDER_REASON_SCORE = ", p={score:.2f}"
"""A decision's reason, built from validated fields only: the decider's name, the first id and its score."""

DECIDER_MAX_RESPONSE_BYTES = 256 * 1024
"""The largest answer read from a decision API: a chat completion may carry a model's reasoning beside it."""

LLM_TIMEOUT_S = 3.0
"""Seconds the ``llm`` router waits for its model when ``router_config`` sets no ``timeout_s``."""

SYSTEMONE_TIMEOUT_S = 1.0
"""Seconds the ``systemone`` router waits when ``router_config`` sets no ``timeout_s``."""

LLM_ROUTE_TOOLS = ("research", "browse")
"""The tools the ``llm`` router orders when neither the caller nor ``router_config`` names any: at a p50 over a
second it costs more than a quarter of a search or an answer (``benchmarks/providers/router_latency.py``)."""

SYSTEMONE_ROUTE_TOOLS = ("answer", "extract", "research", "browse")
"""The tools the ``systemone`` router orders by default: every tool but search, which it would slow down."""

LLM_CHAT_PATH = "/chat/completions"

MODE_JSON_SCHEMA = "json_schema"
MODE_JSON_OBJECT = "json_object"
MODE_PROMPT = "prompt"
LLM_MODES = (MODE_JSON_SCHEMA, MODE_JSON_OBJECT, MODE_PROMPT)
"""How the ``llm`` router asks for JSON: a strict schema, JSON mode, or only the prompt's words. A preset picks
one, ``prompt`` for a ``base_url`` of your own; ``response_format`` in ``router_config`` overrides it."""

LLM_PRESETS: dict[str, tuple[str, str, str]] = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", MODE_JSON_SCHEMA),
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY", MODE_JSON_OBJECT),
    "anthropic": ("https://api.anthropic.com/v1", "ANTHROPIC_API_KEY", MODE_JSON_SCHEMA),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", MODE_JSON_SCHEMA),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", MODE_JSON_OBJECT),
}
"""``llm`` preset -> (base URL, key variable, structured-output mode). Anthropic's compatible endpoint takes
only a strict ``json_schema``; DeepSeek, and Groq on every model, take ``json_object``. A ``base_url`` with no
preset asks in the prompt only, which any server reads."""

SYSTEMONE_DEFAULT_PATH = "/v1/systemone"
SYSTEMONE_PRESETS: dict[str, tuple[str, str, str, str]] = {
    "typesafe": ("https://api.typesafe.ai", SYSTEMONE_DEFAULT_PATH, "jev-1.14.0", "TYPESAFE_API_KEY"),
}
"""``systemone`` preset -> (base URL, path, pinned model, key variable)."""

KEY_HOMES: dict[str, str] = {
    "OPENAI_API_KEY": "api.openai.com",
    "DEEPSEEK_API_KEY": "api.deepseek.com",
    "ANTHROPIC_API_KEY": "api.anthropic.com",
    "OPENROUTER_API_KEY": "openrouter.ai",
    "GROQ_API_KEY": "api.groq.com",
    "TYPESAFE_API_KEY": "api.typesafe.ai",
}
"""A preset's key variable -> the one host it is sent to: a vendor's key never goes to another server."""

SYSTEMONE_QUESTION = "route"
"""The name of the one ``choice`` question a ``systemone`` request asks."""

LLM_REPLY_FORMAT = (
    "Reply with a json object only: "
    '{"ranked": [option ids, best first], "scores": [{"id": "<option id>", "p": <0..1>}], "confidence": <0..1>}. '
    "Give one score for each ranked id, the probability it is the best choice. Leave out any option that does "
    "not fit."
)
"""What the model is told to answer. Its own words are never read: no reason field is asked for."""

MSG_DECIDER_FORM = "DeciderRouter takes an object with decide(question) or adecide(question); got {kind}."
MSG_DECIDER_REPLY = "The decider {name!r} answered something other than a DeciderReply."
MSG_DECIDER_RANKED = "The decider {name!r} answered a ranking that is not a list of the offered option ids, once each."
MSG_DECIDER_SCORES = "The decider {name!r} answered scores that are not numbers from 0 to 1 for ranked ids."
MSG_DECIDER_CONFIDENCE = "The decider {name!r} answered a confidence that is not a number from 0 to 1."
MSG_DECIDER_PRESET = "router={name!r} has no preset {preset!r}; known: {known}."
MSG_DECIDER_BOTH = "router={name!r} takes a preset or a base_url, not both."
MSG_DECIDER_NO_BASE = "router={name!r} needs router_config['preset'] or router_config['base_url']."
MSG_DECIDER_NO_KEY_ENV = (
    "router={name!r} with a base_url off this machine needs router_config['api_key_env'], the variable "
    "holding its key, or api_key."
)
MSG_DECIDER_KEY_UNSET = "router={name!r}: {env} is not set; set it, or pass router_config['api_key']."
MSG_DECIDER_KEY_HOME = "router={name!r}: {env} is sent only to {home}; name another variable for this base_url."
MSG_DECIDER_KEY = "router={name!r}: its key must be a string of visible ASCII characters."
MSG_DECIDER_ENV_NAME = "router={name!r}: router_config['api_key_env'] must name an environment variable."
MSG_DECIDER_MODE = "router={name!r}: router_config['response_format'] takes one of {known}; got {mode!r}."
MSG_DECIDER_TEXT = "router={name!r}: router_config[{field!r}] must be a non-empty string."
DECIDER_WHY_REPLY = "an answer that is not the decision API's JSON shape"
