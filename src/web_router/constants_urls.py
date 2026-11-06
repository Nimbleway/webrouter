"""Constants for finding URLs in prose and judging their hosts. Kept apart so ``constants.py`` stops growing."""

URL_TRAILING_PUNCTUATION = ".,;:!?"
"""Stripped from a URL found in prose: the end of a sentence, not part of the address."""

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
