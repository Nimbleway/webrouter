"""What a URL's host is: loopback, a cloud metadata endpoint, or a private network -- read as a browser reads it.

Shared by the MCP browser tools' navigation guard and the routers'
``url_policy``. Every check here reads the host as written: a literal address
in any spelling a browser accepts (decimal, hex, octal, short IPv4, and IPv4
mapped into IPv6) is judged as the address it is, and nothing resolves DNS.
"""

from __future__ import annotations

import ipaddress
import re
import socket

from web_router.constants_urls import (
    IPV4_COMPATIBLE_PREFIX,
    LEGACY_IPV4_PATTERN,
    LOOPBACK_NAMES,
    METADATA_HOSTS,
    NAT64_PREFIX,
)

_LEGACY_IPV4 = re.compile(LEGACY_IPV4_PATTERN)
_BARE_HEX = re.compile(r"(?:^|(?<=\.))0x(?=\.|$)")


def host_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """``host`` as the address it spells, an IPv4-mapped IPv6 address as its IPv4; ``None`` for a name."""
    address: ipaddress.IPv4Address | ipaddress.IPv6Address
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        legacy = legacy_ipv4(host)
        if legacy is None:
            return None
        address = legacy
    if isinstance(address, ipaddress.IPv6Address):
        return _embedded_ipv4(address) or address
    return address


def _embedded_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 one carries and reaches: IPv4-mapped, NAT64 (``64:ff9b::/96``) or the deprecated
    IPv4-compatible form (``::a.b.c.d``, but not ``::`` or ``::1``, which are IPv6's own).

    A 6to4 address (``2002::/16``) gives its IPv4 only when that is no public host's: older Pythons call every
    6to4 address global, newer ones none, so the IPv4 decides a non-public one and the rest stay as before.
    """
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    sixtofour = address.sixtofour
    if sixtofour is not None and (not sixtofour.is_global or sixtofour.is_multicast):
        return sixtofour
    low = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    if address in NAT64_PREFIX:
        return low
    if address in IPV4_COMPATIBLE_PREFIX and int(address) > 1:
        return low
    return None


def numeric_host(host: str) -> bool:
    """Whether ``host`` has an IPv4 address's shape: up to four dot-separated parts, each digits or ``0x`` hex.

    Whether or not ``inet_aton`` reads it: ``127.0.0.09`` is no octal, but macOS's getaddrinfo reads it as decimal.
    """
    return _LEGACY_IPV4.fullmatch(host) is not None


def legacy_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """``host`` read as a browser reads an IPv4 address: ``2130706433``, ``0x7f000001``, ``127.1`` or ``0177.0.0.1``.

    ``ipaddress`` accepts only dotted quads, but a browser canonicalizes all of
    these to one before it connects, so each is checked as the address it is.
    """
    if not numeric_host(host):
        return None
    try:
        # A browser reads a bare ``0x`` part as 0; inet_aton may refuse it or not, by platform.
        return ipaddress.IPv4Address(socket.inet_aton(_BARE_HEX.sub("0x0", host)))
    except (OSError, ValueError):
        return None


def is_loopback(host: str) -> bool:
    """``localhost``, ``0.0.0.0``, or any loopback or unspecified address."""
    address = host_address(host)
    return host in LOOPBACK_NAMES or (address is not None and (address.is_loopback or address.is_unspecified))


def is_metadata(host: str) -> bool:
    """A cloud metadata endpoint by name, or any link-local address."""
    address = host_address(host)
    # All of link-local, not only the one address: the metadata service answers on 169.254/16 on several clouds.
    return host in METADATA_HOSTS or (address is not None and address.is_link_local)


def is_private(host: str) -> bool:
    """A literal address no public host has: private (RFC 1918, ULA), shared (CGNAT), reserved, or multicast.

    Read as :func:`ipaddress.ip_address` reads ``is_global``, so loopback and
    link-local are in it too. A name is never private here: nothing resolves it.
    """
    address = host_address(host)
    return address is not None and (not address.is_global or address.is_multicast)
