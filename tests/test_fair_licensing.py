"""Unit tests for FairLicensingCollector (CASS Section 4.2.2)."""

import pytest

from collectors.sustainability.fair_licensing import (
    FairLicensingCollector, _CITATION_FIELDS,
)


@pytest.fixture
def collector():
    return FairLicensingCollector()


HDF5_LICENSE = """Copyright Notice and License Terms for HDF5

Copyright 2006 by The HDF Group.
Copyright 1998-2006 by The Board of Trustees of the University of Illinois.
All rights reserved.

This software library and utilities is covered by the 3-clause BSD License.
"""


class TestLicenseTextResolution:
    def test_recovers_family_the_api_could_not_name(self, collector):
        # GitHub returns NOASSERTION for HDF5 because of the extra copyright
        # notices, which would otherwise record the project as unlicensed.
        out = collector._analyze_license_text(
            {"spdx_id": "NOASSERTION", "text": HDF5_LICENSE}
        )
        assert out["resolved_from_text"] == "BSD-3-Clause"
        assert out["identified"] is True
        assert out["api_classified"] is False

    def test_api_classification_is_trusted_when_present(self, collector):
        out = collector._analyze_license_text({"spdx_id": "Apache-2.0", "text": ""})
        assert out["api_classified"] is True
        assert out["resolved_from_text"] is None

    def test_specific_family_wins_over_generic(self, collector):
        out = collector._analyze_license_text(
            {"spdx_id": None, "text": "the 3-clause BSD License applies"}
        )
        assert out["resolved_from_text"] == "BSD-3-Clause"

    def test_unrecognisable_text(self, collector):
        out = collector._analyze_license_text(
            {"spdx_id": "NOASSERTION", "text": "All rights reserved. Contact us."}
        )
        assert out["resolved_from_text"] is None
        assert out["identified"] is False

    def test_exception_markers(self, collector):
        out = collector._analyze_license_text(
            {"spdx_id": "Apache-2.0", "text": "Apache 2.0 with LLVM exceptions apply"}
        )
        assert "Named exception" in out["exception_markers"]

    def test_multiple_copyright_holders_flagged(self, collector):
        # Copyright lines are separated by blank lines in real licences, so
        # they are counted rather than matched as consecutive lines.
        out = collector._analyze_license_text({"spdx_id": None, "text": HDF5_LICENSE})
        assert "Multiple copyright holders" in out["exception_markers"]

    def test_single_copyright_holder_not_flagged(self, collector):
        out = collector._analyze_license_text(
            {"spdx_id": "MIT", "text": "Copyright 2026 Someone\n\nMIT License"})
        assert "Multiple copyright holders" not in out["exception_markers"]


class TestCitationMetadata:
    def test_missing_file(self, collector):
        out = collector._analyze_citation({})
        assert out["exists"] is False
        assert out["missing"] == _CITATION_FIELDS

    def test_top_level_doi(self, collector):
        out = collector._analyze_citation({"title": "x", "doi": "10.1/abc"})
        assert "doi" in out["present"]

    def test_doi_inside_identifiers(self, collector):
        out = collector._analyze_citation(
            {"title": "x", "identifiers": [{"type": "doi", "value": "10.1/abc"}]}
        )
        assert "doi" in out["present"]

    def test_non_doi_identifier_does_not_count(self, collector):
        out = collector._analyze_citation(
            {"title": "x", "identifiers": [{"type": "url", "value": "http://x"}]}
        )
        assert "doi" not in out["present"]

    def test_empty_field_is_not_present(self, collector):
        out = collector._analyze_citation({"title": "x", "version": ""})
        assert "version" not in out["present"]


class TestFairPrinciples:
    def _assess(self, collector, **kw):
        base = dict(
            license_data={}, exceptions={"identified": True},
            citation={}, metadata={"exists": True, "present": ["doi"]},
            has_codemeta=False, has_zenodo=False, releases=True,
        )
        base.update(kw)
        return collector._assess_fair(**base)

    def test_all_four(self, collector):
        assert self._assess(collector)["count"] == 4

    def test_findable_needs_a_persistent_identifier(self, collector):
        out = self._assess(collector, metadata={"exists": True, "present": ["title"]})
        assert "Findable" not in out["satisfied"]

    def test_zenodo_file_also_makes_it_findable(self, collector):
        out = self._assess(
            collector, metadata={"exists": True, "present": []}, has_zenodo=True)
        assert "Findable" in out["satisfied"]

    def test_reusable_needs_releases(self, collector):
        assert "Reusable" not in self._assess(collector, releases=False)["satisfied"]

    def test_unidentified_licence_breaks_two_principles(self, collector):
        out = self._assess(collector, exceptions={"identified": False})
        assert "Accessible" not in out["satisfied"]
        assert "Reusable" not in out["satisfied"]


class TestScoring:
    def test_thresholds(self, collector):
        exceptions = {"identified": True, "api_classified": True,
                      "api_spdx": "MIT", "exception_markers": []}
        metadata = {"exists": True, "present": ["title", "authors", "doi", "version"]}
        fair = {"count": 3, "satisfied": ["Findable", "Accessible", "Reusable"]}
        s = collector._calculate_score(exceptions, metadata, fair)
        assert s["score"] == 3 and s["max_score"] == 3

    def test_metadata_below_threshold_fails(self, collector):
        s = collector._calculate_score(
            {"identified": True, "api_classified": True, "api_spdx": "MIT",
             "exception_markers": []},
            {"exists": True, "present": ["title", "authors", "doi"]},
            {"count": 4, "satisfied": []},
        )
        assert not s["sub_scores"]["fair_metadata"]["passing"]
