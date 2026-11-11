"""Loading the vendor catalog and looking things up in it.

The built-in files ship inside the package; a directory named by
``WEB_ROUTER_VENDOR_CATALOG`` is read after them, a file there replacing the
built-in one of the same vendor. A file that does not validate fails the load
with its name and the reason: a catalog that quietly dropped a vendor would
answer "unknown vendor" for a typo nobody could find.
"""

from __future__ import annotations

import functools
import json
import os
import re
from collections.abc import Iterable
from importlib import resources
from pathlib import Path

from pydantic import ValidationError

from web_router.constants_vendors import (
    MSG_VENDOR_CATALOG_ID_MISMATCH,
    MSG_VENDOR_CATALOG_INVALID,
    MSG_VENDOR_ENDPOINT_HINT,
    MSG_VENDOR_ENDPOINT_NO_VENDOR,
    MSG_VENDOR_ENDPOINT_UNKNOWN,
    MSG_VENDOR_UNKNOWN,
    VENDOR_CATALOG_DIR,
    VENDOR_ENV_CATALOG_DIR,
    VENDOR_FIND_STOP_WORDS,
)
from web_router.exceptions import ConfigurationError
from web_router.exceptions_vendors import VendorNotFoundError
from web_router.vendors.schema import Vendor, VendorEndpoint

_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> set[str]:
    """Lowercase words, plurals folded: "monitors" and "monitor" are one word to a search."""
    return {
        word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word
        for word in _WORD.findall(text.lower())
    }


class VendorCatalog:
    """Every vendor file, validated and indexed by vendor and endpoint id."""

    def __init__(self, vendors: Iterable[Vendor]) -> None:
        self._vendors: dict[str, Vendor] = {vendor.vendor: vendor for vendor in vendors}
        self._endpoints: dict[str, VendorEndpoint] = {
            endpoint.id: endpoint for vendor in self._vendors.values() for endpoint in vendor.endpoints
        }

    def __len__(self) -> int:
        return len(self._vendors)

    def names(self) -> list[str]:
        return sorted(self._vendors)

    def all(self) -> list[Vendor]:
        return [self._vendors[name] for name in self.names()]

    def vendor(self, name: str) -> Vendor:
        try:
            return self._vendors[name]
        except KeyError:
            raise VendorNotFoundError(
                name, MSG_VENDOR_UNKNOWN.format(vendor=name, known=", ".join(self.names()))
            ) from None

    def endpoint(self, endpoint_id: str) -> VendorEndpoint:
        found = self._endpoints.get(endpoint_id)
        if found is not None:
            return found
        vendor_name = endpoint_id.split(".", 1)[0]
        vendor = self._vendors.get(vendor_name)
        if vendor is None:
            hint = MSG_VENDOR_ENDPOINT_NO_VENDOR.format(vendor=vendor_name)
        else:
            hint = MSG_VENDOR_ENDPOINT_HINT.format(
                vendor=vendor_name, known=", ".join(endpoint.id for endpoint in vendor.endpoints)
            )
        raise VendorNotFoundError(endpoint_id, MSG_VENDOR_ENDPOINT_UNKNOWN.format(endpoint=endpoint_id, hint=hint))

    def find(
        self,
        query: str,
        *,
        vendor: str | None = None,
        category: str | None = None,
        kind: str | None = None,
        limit: int | None = None,
        min_score: int = 0,
    ) -> list[VendorEndpoint]:
        """Endpoints whose words match ``query``, best match first, scoring at least ``min_score``.

        A plain word-overlap score over the id, name, summary, job and the
        vendor's own name and category, with filler words ("a", "for", "the")
        left out of the query (a query of nothing else finds nothing) -- enough to go from "google ads spend
        by campaign" to the GAQL endpoint without a model in the loop, and
        deterministic, so the same query lists the same endpoints.
        """
        if vendor is not None:
            self.vendor(vendor)
        words = _words(query) - VENDOR_FIND_STOP_WORDS
        if not words and _words(query):
            return []  # a query of filler words only asks for nothing, not for everything
        scored: list[tuple[int, str, VendorEndpoint]] = []
        for endpoint in self._endpoints.values():
            owner = self._vendors[endpoint.vendor]
            if vendor is not None and owner.vendor != vendor:
                continue
            if category is not None and owner.category != category:
                continue
            if kind is not None and endpoint.kind != kind:
                continue
            score = _score(words, endpoint, owner)
            if (words and score == 0) or score < min_score:
                continue
            scored.append((-score, endpoint.id, endpoint))
        scored.sort(key=lambda row: (row[0], row[1]))
        found = [endpoint for _, _, endpoint in scored]
        return found if limit is None else found[:limit]


def _score(words: set[str], endpoint: VendorEndpoint, vendor: Vendor) -> int:
    if not words:
        return 0
    strong = _words(f"{endpoint.id} {endpoint.job or ''} {vendor.name} {vendor.vendor}")
    weak = _words(f"{endpoint.name} {endpoint.summary} {vendor.category}")
    return 3 * len(words & strong) + len(words & weak)


def load_vendor_file(path: Path | str, text: str | None = None) -> Vendor:
    """Parse and validate one vendor file, naming the file in any error."""
    path = Path(path)
    try:
        payload = json.loads(text if text is not None else path.read_text(encoding="utf-8"))
        vendor = Vendor.model_validate(payload)
    except (ValueError, ValidationError) as exc:
        raise ConfigurationError(MSG_VENDOR_CATALOG_INVALID.format(file=path.name, error=exc)) from exc
    if vendor.vendor != path.stem:
        error = MSG_VENDOR_CATALOG_ID_MISMATCH.format(declared=vendor.vendor, stem=path.stem)
        raise ConfigurationError(MSG_VENDOR_CATALOG_INVALID.format(file=path.name, error=error))
    return vendor


def _builtin() -> dict[str, Vendor]:
    root = resources.files("web_router") / VENDOR_CATALOG_DIR
    if not root.is_dir():
        return {}
    found: dict[str, Vendor] = {}
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.endswith(".json"):
            vendor = load_vendor_file(Path(entry.name), entry.read_text(encoding="utf-8"))
            found[vendor.vendor] = vendor
    return found


def _extra(directory: str) -> dict[str, Vendor]:
    root = Path(directory).expanduser()
    if not root.is_dir():
        raise ConfigurationError(f"{VENDOR_ENV_CATALOG_DIR}={directory!r} is not a directory")
    return {vendor.vendor: vendor for vendor in (load_vendor_file(path) for path in sorted(root.glob("*.json")))}


def builtin_catalog() -> VendorCatalog:
    """Only the vendors that ship with the package, whatever ``WEB_ROUTER_VENDOR_CATALOG`` adds: what the committed
    ``VENDORS.md`` and ``.env.example`` are generated from."""
    return _load(None)


@functools.lru_cache(maxsize=4)
def _load(extra_dir: str | None) -> VendorCatalog:
    vendors = _builtin()
    if extra_dir:
        vendors.update(_extra(extra_dir))
    return VendorCatalog(vendors.values())


def get_catalog() -> VendorCatalog:
    """The catalog: built-in vendors, plus any directory ``WEB_ROUTER_VENDOR_CATALOG`` names. Read once per value."""
    return _load(os.environ.get(VENDOR_ENV_CATALOG_DIR) or None)


def reload_catalog() -> VendorCatalog:
    """Forget the cached catalog and read the files again."""
    _load.cache_clear()
    return get_catalog()
