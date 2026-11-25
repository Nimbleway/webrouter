"""An import cannot carry a changed provider file under its recorded checksum."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import data_driven_router as importer
from scripts.data_driven_manifest import PROVIDER_FILE
from tests import test_data_driven_lookup_release as delivery_fixtures

release = delivery_fixtures.release


def test_a_changed_carried_provider_file_stops_publication(release: tuple[Path, Path, Path]) -> None:
    source, output, table = release
    importer.import_release(source, "0.1.1", [], table)
    providers = output / PROVIDER_FILE
    providers.write_bytes(providers.read_bytes() + b" ")
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    with pytest.raises(ValueError, match="does not match its manifest checksum"):
        importer.import_release(source, "0.1.1", [])
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before
    importer.import_release(source, "0.1.1", [], table)
    importer.import_release(source, "0.1.1", [])
