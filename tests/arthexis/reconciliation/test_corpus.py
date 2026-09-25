"""Tests for the accepted preserved reconciliation fixture corpus."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arthexis.reconciliation.corpus import (
    inspect_preserved_corpus,
    inspect_preserved_fixture,
)
from arthexis.reconciliation.preservation import preserve_capture
from tests.arthexis.reconciliation.test_preservation import _capture


def test_repository_preserved_fixture_corpus_is_valid():
    # The corpus may be empty until the first reviewed field fixture is accepted.
    # Once fixtures are committed, every finalized bundle is verified here on all
    # supported Python versions by the normal compatibility workflow.
    inspect_preserved_corpus()


def test_corpus_inspection_requires_preservation_provenance(tmp_path):
    capture = _capture(tmp_path)

    with pytest.raises(ValueError, match="preservation metadata"):
        inspect_preserved_fixture(capture.path)


def test_preserved_fixture_is_accepted_by_corpus_contract(tmp_path):
    capture = _capture(tmp_path)
    preserved = preserve_capture(capture.path, tmp_path / "preserved")

    fixture = inspect_preserved_fixture(preserved.path)

    assert fixture.name == preserved.path.name
    assert fixture.source_capture_id == capture.capture_id
    assert fixture.source_version == "1.7.3"


def test_corpus_rejects_manifest_that_claims_source_paths_are_exported(tmp_path):
    capture = _capture(tmp_path)
    preserved = preserve_capture(capture.path, tmp_path / "preserved")
    manifest_path = preserved.path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["paths_exported"] = True
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    # This intentionally invalidates the finalized bundle checksum first,
    # proving accepted fixtures remain immutable after finalization.
    with pytest.raises(ValueError, match="manifest checksum"):
        inspect_preserved_fixture(preserved.path)
