"""Endpoint text is tokenized once per catalog, on the first find, never again."""

from __future__ import annotations

import pytest

from web_router.vendors import catalog as catalog_module
from web_router.vendors.catalog import VendorCatalog


def test_a_second_find_does_not_tokenize_endpoint_text(catalog: VendorCatalog, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    tokenize = catalog_module.words

    def counting(text: str) -> frozenset[str]:
        calls.append(text)
        return tokenize(text)

    monkeypatch.setattr(catalog_module, "words", counting)
    catalog.find("monitor")
    assert len(calls) > 1  # the query and every endpoint text
    calls.clear()
    catalog.find("monitor")
    assert calls == ["monitor"]  # the query only: endpoint text is already tokenized


def test_words_stays_frozen() -> None:
    assert isinstance(catalog_module.words("Stripe Payments API"), frozenset)
