"""The built-in provider names, re-exported by :mod:`web_router.types`."""

from enum import Enum


class Provider(str, Enum):
    """The providers built into web-router.

    Every argument that names a provider also accepts a plain string; this exists so the built-in names are
    discoverable from an editor rather than remembered. Backends added with :func:`web_router.register_provider`
    are named by string, which is why the string form is never going away.

    Not every name serves every method: ``oxylabs``, ``brightdata``, ``firecrawl``, ``jina`` and ``obscura`` only
    extract, and most search backends do not extract. ``steel``, ``browserbase``, ``hyperbrowser``, ``anchor``,
    ``kernel`` and ``local`` only open browser sessions, ``browser-use`` and ``skyvern`` only run browse tasks, and
    ``apify`` only runs live data. :func:`web_router.available_providers` and its per-method counterparts list each
    method's own set. ``auto`` tries its backends in turn for every method; ``max`` races them all, for extract alone.
    """

    # Keep the public pickle path stable after moving the definition.
    __module__ = "web_router.types"

    NIMBLE = "nimble"
    TAVILY = "tavily"
    YOU = "you"
    BRAVE = "brave"
    CONTEXT = "context"
    EXA = "exa"
    PARALLEL = "parallel"
    PERPLEXITY = "perplexity"
    LINKUP = "linkup"
    SELTZ = "seltz"
    WEBIQ = "webiq"
    OCTEN = "octen"
    KEIROLABS = "keirolabs"
    KEENABLE = "keenable"
    DEEPSEEK = "deepseek"
    VALYU = "valyu"
    SPECSTREAM = "specstream"
    AGENTCORE = "agentcore"
    YOU_MCP = "you-mcp"
    EXA_MCP = "exa-mcp"
    PARALLEL_MCP = "parallel-mcp"
    EXA_LITELLM = "exa-litellm"
    TAVILY_LITELLM = "tavily-litellm"
    PARALLEL_LITELLM = "parallel-litellm"
    OXYLABS = "oxylabs"
    BRIGHTDATA = "brightdata"
    FIRECRAWL = "firecrawl"
    JINA = "jina"
    OBSCURA = "obscura"
    STEEL = "steel"
    BROWSERBASE = "browserbase"
    HYPERBROWSER = "hyperbrowser"
    ANCHOR = "anchor"
    KERNEL = "kernel"
    SKYVERN = "skyvern"
    LOCAL = "local"
    APIFY = "apify"
    BROWSER_USE = "browser-use"
    AUTO = "auto"
    MAX = "max"

    # A str-mixin enum otherwise renders as "Provider.NIMBLE" in f-strings and
    # str(), which would reach users through error messages and `raw` payloads.
    __str__ = str.__str__
