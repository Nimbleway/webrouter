"""Constants for finding URLs in prose and judging their hosts. Kept apart so ``constants.py`` stops growing."""

URL_TRAILING_PUNCTUATION = ".,;:!?"
"""Stripped from a URL found in prose: the end of a sentence, not part of the address."""

URL_BRACKETS = {")": "(", "]": "[", "}": "{"}
"""Each closing bracket and its opener: a URL in prose keeps a closing bracket only when it opened it."""

METADATA_HOSTS: frozenset[str] = frozenset(
    {
        "169.254.169.254",
        "fd00:ec2::254",
        "169.254.170.2",
        "100.100.100.200",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
"""Cloud metadata endpoints, which hand out the machine's own credentials to whoever asks from inside it."""

LOOPBACK_NAMES: frozenset[str] = frozenset({"localhost", "0.0.0.0"})
"""Hostnames treated as the loopback interface, beside every 127/8 and ``::1`` address."""

LEGACY_IPV4_PATTERN = r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+)){0,3}"
"""The decimal, hex, octal and short IPv4 spellings a browser accepts, as a lowercased host.

Only a host of this shape is handed to ``inet_aton``, which would otherwise be
asked to read names it has no business reading.
"""

URL_POLICY_PUBLIC_ONLY = "public_only"
"""The ``url_policy`` that refuses private, loopback, link-local and cloud metadata hosts before a call."""

URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})
"""The schemes a routed call's URLs may use, with any ``url_policy`` or none."""

LOCALHOST_SUFFIX = ".localhost"
"""Every name under ``localhost`` is loopback (RFC 6761)."""

URL_REFUSED_KEY = "url:{host}"
"""How the decision record's ``dropped`` names a URL the policy refused: by scheme and host, never in full."""

MSG_URL_POLICY = "url_policy takes {known} or None; got {got!r}."
MSG_URL_SCHEME = "the scheme {scheme!r} is not http or https"
MSG_URL_UNPARSEABLE = "the URL cannot be parsed"
MSG_URL_LOOPBACK = "the host is loopback"
MSG_URL_METADATA = "the host is a cloud metadata endpoint"
MSG_URL_PRIVATE = "the host is a private, shared or reserved address"
MSG_URL_AMBIGUOUS = "a browser could read the host differently from how it is checked"

AUTHORITY_REFUSED = "\\@%"
"""Characters ``public_only`` refuses in a URL's authority: a browser ends the host at a backslash, ``@`` brings
userinfo, and a browser decodes a ``%``-encoded host, so each can make it connect somewhere other than checked."""

MSG_PLAN_EXTRACT_NO_URL = "plan(tool='extract') found no http(s) URL in the prompt: write the URL into it."
MSG_PLAN_EXTRACT_REFUSED = "plan(tool='extract') found only URLs url_policy refuses ({refused})."
