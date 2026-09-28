"""Unit tests for FairLicensingCollector (CASS Section 4.2.2)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from collectors.ecosystem.base import COLLECTION_GAP, RepoTree
from collectors.ecosystem.fair_licensing import (
    FairLicensingCollector, _CITATION_FIELDS, _CODEMETA_PATHS,
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

AMREX_LICENSE = """AMReX Copyright (c) 2024, The Regents of the University of California,
through Lawrence Berkeley National Laboratory. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

(1) Redistributions of source code must retain the above copyright notice,
this list of conditions and the following disclaimer.

(2) Redistributions in binary form must reproduce the above copyright
notice, this list of conditions and the following disclaimer in the
documentation and/or other materials provided with the distribution.

(3) Neither the name of the copyright holder nor the names of its
contributors may be used to endorse or promote products derived from this
software without specific prior written permission.
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

    def test_unnamed_bsd3_body_is_recognised_by_its_clauses(self, collector):
        # AMReX: verbatim BSD-3-Clause, "(1)" numbering, never says "BSD".
        out = collector._analyze_license_text({"spdx_id": "NOASSERTION", "text": AMREX_LICENSE})
        assert out["resolved_from_text"] == "BSD-3-Clause"
        assert out["resolved_via"] == "clauses"
        assert out["identified"] is True

    def test_bsd2_body_without_endorsement_clause(self, collector):
        text = AMREX_LICENSE.split("(3)")[0]
        out = collector._analyze_license_text({"spdx_id": "NOASSERTION", "text": text})
        assert out["resolved_from_text"] == "BSD-2-Clause"

    def test_unnamed_mit_body(self, collector):
        text = ("Permission is hereby granted, free of charge, to any person\nobtaining a copy "
                "of this software ...\nThe above copyright notice and this permission notice\n"
                "shall be included in all copies.")
        out = collector._analyze_license_text({"spdx_id": "NOASSERTION", "text": text})
        assert out["resolved_from_text"] == "MIT"

    def test_name_in_text_wins_over_clauses(self, collector):
        text = "Covered by the 3-clause BSD License.\n" + AMREX_LICENSE
        out = collector._analyze_license_text({"spdx_id": "NOASSERTION", "text": text})
        assert out["resolved_via"] == "text"

    def test_citation_declaration_is_the_last_resort(self, collector):
        out = collector._analyze_license_text(
            {"spdx_id": "NOASSERTION", "text": "All rights reserved."}, declared="BSD-3-Clause",
        )
        assert out["resolved_from_text"] == "BSD-3-Clause"
        assert out["resolved_via"] == "citation"

    def test_citation_declaration_list_and_variants(self, collector):
        out = collector._analyze_license_text(
            {"spdx_id": None, "text": ""}, declared=["LicenseRef-custom", "GPL-3.0-or-later"],
        )
        assert out["resolved_from_text"] == "GPL"

    def test_non_osi_declaration_stays_unresolved(self, collector):
        # The dashboard reads any resolved family as OSI-approved.
        out = collector._analyze_license_text(
            {"spdx_id": "NOASSERTION", "text": ""}, declared="LicenseRef-proprietary",
        )
        assert out["identified"] is False

    def test_declaration_does_not_override_api_classification(self, collector):
        out = collector._analyze_license_text({"spdx_id": "MIT", "text": ""}, declared="GPL-3.0")
        assert out["resolved_from_text"] is None
        assert out["api_classified"] is True

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
            exceptions={"identified": True},
            metadata={"exists": True, "present": ["doi"]},
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


class TestOrGapAndAndGap:
    def test_or_any_confirmed_true_wins(self, collector):
        assert collector._or_gap((True, False), (False, True)) == (True, False)

    def test_or_no_true_but_an_uncertain_operand_is_uncertain(self, collector):
        assert collector._or_gap((False, False), (False, True)) == (False, True)

    def test_or_all_confirmed_false_is_confirmed_false(self, collector):
        assert collector._or_gap((False, False), (False, False)) == (False, False)

    def test_and_any_confirmed_false_wins_even_with_an_uncertain_operand(self, collector):
        assert collector._and_gap((False, False), (True, True)) == (False, False)

    def test_and_no_false_but_an_uncertain_operand_is_uncertain(self, collector):
        assert collector._and_gap((True, False), (True, True)) == (False, True)

    def test_and_all_confirmed_true_is_confirmed_true(self, collector):
        assert collector._and_gap((True, False), (True, False)) == (True, False)


class TestAssessFairGapHandling:
    def _assess(self, collector, **kw):
        base = dict(
            exceptions={"identified": True},
            metadata={"exists": True, "present": ["doi"]},
            has_codemeta=False, has_zenodo=False, releases=True,
        )
        base.update(kw)
        return collector._assess_fair(**base)

    def test_findable_uncertain_when_zenodo_gapped_and_no_doi(self, collector):
        out = self._assess(
            collector, metadata={"exists": True, "present": []},
            has_zenodo=False, zenodo_gap=True,
        )
        assert out["principles"]["Findable"] is False
        assert out["principle_gaps"]["Findable"] is True

    def test_findable_confirmed_false_survives_a_zenodo_gap_if_not_actually_gapped(self, collector):
        # has_zenodo confirmed False (zenodo_gap=False) and no doi -- a real negative.
        out = self._assess(collector, metadata={"exists": True, "present": []})
        assert out["principles"]["Findable"] is False
        assert out["principle_gaps"]["Findable"] is False

    def test_accessible_uncertain_when_license_gapped(self, collector):
        out = self._assess(
            collector, exceptions={"identified": False, "not_collected": True},
        )
        assert out["principles"]["Accessible"] is False
        assert out["principle_gaps"]["Accessible"] is True

    def test_reusable_confirmed_false_when_license_confirmed_unidentified_even_if_releases_gapped(self, collector):
        # AND with a confirmed-False operand (identified) can't become True
        # no matter what the other (releases) turns out to be.
        out = self._assess(
            collector, exceptions={"identified": False},
            releases=False, releases_gap=True,
        )
        assert out["principles"]["Reusable"] is False
        assert out["principle_gaps"]["Reusable"] is False

    def test_reusable_uncertain_when_releases_gapped_and_license_identified(self, collector):
        out = self._assess(
            collector, exceptions={"identified": True},
            releases=False, releases_gap=True,
        )
        assert out["principles"]["Reusable"] is False
        assert out["principle_gaps"]["Reusable"] is True

    def test_interoperable_uncertain_when_codemeta_gapped_and_no_citation(self, collector):
        out = self._assess(
            collector, metadata={"exists": False, "present": [], "not_collected": True},
            has_codemeta=False, codemeta_gap=True,
        )
        assert out["principles"]["Interoperable"] is False
        assert out["principle_gaps"]["Interoperable"] is True


class TestScoringGapHandling:
    def test_below_threshold_fair_score_under_gap_is_not_collected(self, collector):
        fair = {
            "count": 1, "satisfied": ["Accessible"],
            "principle_gaps": {"Findable": True, "Accessible": False,
                               "Interoperable": False, "Reusable": False},
        }
        s = collector._calculate_score(
            {"identified": True}, {"exists": False, "present": []}, fair,
        )
        entry = s["sub_scores"]["fair4rs_assessment"]
        assert entry["passing"] is False
        assert entry["not_collected"] is True

    def test_threshold_already_met_survives_gapped_principles(self, collector):
        fair = {
            "count": 3, "satisfied": ["Accessible", "Interoperable", "Reusable"],
            "principle_gaps": {"Findable": True, "Accessible": False,
                               "Interoperable": False, "Reusable": False},
        }
        s = collector._calculate_score(
            {"identified": True}, {"exists": True, "present": ["doi"]}, fair,
        )
        entry = s["sub_scores"]["fair4rs_assessment"]
        assert entry["passing"] is True
        assert "not_collected" not in entry

    def test_unidentified_license_under_gap_is_not_collected(self, collector):
        s = collector._calculate_score(
            {"identified": False, "not_collected": True, "exception_markers": []},
            {"exists": False, "present": []},
            {"count": 0, "satisfied": [], "principle_gaps": {}},
        )
        assert s["sub_scores"]["license_exception_handling"]["not_collected"] is True

    def test_metadata_below_threshold_under_gap_is_not_collected(self, collector):
        s = collector._calculate_score(
            {"identified": True, "api_classified": True, "api_spdx": "MIT",
             "exception_markers": []},
            {"exists": False, "present": [], "not_collected": True},
            {"count": 4, "satisfied": [], "principle_gaps": {}},
        )
        assert s["sub_scores"]["fair_metadata"]["not_collected"] is True

    def test_everything_gapped_reports_not_collected_status(self, collector):
        s = collector._calculate_score(
            {"identified": False, "not_collected": True, "exception_markers": []},
            {"exists": False, "present": [], "not_collected": True},
            {"count": 0, "satisfied": [],
             "principle_gaps": {"Findable": True, "Accessible": True,
                                "Interoperable": True, "Reusable": True}},
        )
        assert s["score"] is None
        assert s["max_score"] == 0
        assert s["status"] == "not_collected"


class TestFetchGapHandling:
    def test_get_license_gap_is_tracked(self, collector):
        async def go():
            with patch.object(collector, "_github_get", new=AsyncMock(return_value=COLLECTION_GAP)):
                return await collector._get_license(None, "o", "r")

        data, saw_gap = asyncio.run(go())
        assert saw_gap is True

    def test_get_citation_gapped_tree_is_tracked(self, collector):
        citation, saw_gap = asyncio.run(collector._get_citation(None, "o", "r", COLLECTION_GAP))
        assert citation == {}
        assert saw_gap is True

    def test_get_citation_resolves_case_insensitively(self, collector):
        # Lab-Notebooks/CodeScribe ships citation.cff, not CITATION.cff.
        tree = RepoTree("o", "r", ["citation.cff"], truncated=False)
        data = {"content": "dGl0bGU6IEZvbw=="}  # base64 "title: Foo"

        async def go():
            with patch.object(collector, "_github_get", new=AsyncMock(return_value=data)):
                return await collector._get_citation(None, "o", "r", tree)

        citation, saw_gap = asyncio.run(go())
        assert citation == {"title": "Foo"}
        assert saw_gap is False

    def test_get_citation_confirmed_absent_is_not_a_gap(self, collector):
        tree = RepoTree("o", "r", ["README.md"], truncated=False)
        citation, saw_gap = asyncio.run(collector._get_citation(None, "o", "r", tree))
        assert citation == {}
        assert saw_gap is False

    def test_any_exists_gapped_tree_is_tracked(self, collector):
        found, saw_gap = collector._any_exists(COLLECTION_GAP, _CODEMETA_PATHS)
        assert found is False
        assert saw_gap is True

    def test_any_exists_found_does_not_need_gap_flag(self, collector):
        tree = RepoTree("o", "r", _CODEMETA_PATHS, truncated=False)
        found, saw_gap = collector._any_exists(tree, _CODEMETA_PATHS)
        assert found is True
        assert saw_gap is False

    def test_has_releases_gap_is_tracked(self, collector):
        async def go():
            with patch.object(collector, "_github_get", new=AsyncMock(return_value=COLLECTION_GAP)):
                return await collector._has_releases(None, "o", "r")

        has_releases, saw_gap = asyncio.run(go())
        assert has_releases is False
        assert saw_gap is True


class TestBibtexCitation:
    PAPER = """
```bibtex
@article{gardner2022sundials,
  title   = {Enabling new flexibility in the {SUNDIALS} suite},
  author  = {Gardner, David J and Reynolds, Daniel R},
  journal = {ACM TOMS},
  doi     = {10.1145/3539801}
}
```
"""

    def test_paper_entries_give_title_authors_doi(self, collector):
        result = collector._analyze_bibtex(self.PAPER, "LLNL", "sundials")
        assert result["present"] == ["title", "authors", "doi"]

    def test_software_entry_can_carry_version_and_repository(self, collector):
        text = """@software{pkg,
  title = {Pkg}, author = {A. Person},
  version = {7.4.0},
  url = {https://github.com/LLNL/sundials},
  doi = {10.5281/zenodo.1}
}"""
        result = collector._analyze_bibtex(text, "LLNL", "sundials")
        assert result["present"] == ["title", "authors", "version", "repository-code", "doi"]

    def test_url_to_another_site_is_not_repository_code(self, collector):
        text = "@article{x,\n  url = {https://doi.org/10.1/abc}\n}"
        assert "repository-code" not in collector._analyze_bibtex(text, "o", "r")["present"]

    def _score(self, collector, bibtex):
        metadata = {"exists": False, "present": [], "missing": []}
        fair = {"principles": {}, "principle_gaps": {}, "count": 0}
        return collector._calculate_score({}, metadata, fair, bibtex)["sub_scores"]["fair_metadata"]

    def test_bibtex_fields_are_reported_and_scored(self, collector):
        row = self._score(collector, {"path": "CITATIONS.md", "present": ["title", "authors", "doi"]})
        assert row["value"] == "3/6 citation fields present (BibTeX in CITATIONS.md; no CITATION.cff)"
        assert row["passing"] is False

    def test_no_bibtex_keeps_existing_message(self, collector):
        assert self._score(collector, None)["value"] == "No CITATION.cff found"

    def test_bibtex_does_not_feed_fair4rs(self, collector):
        # A paper DOI identifies the paper, not the software.
        metadata = collector._analyze_citation({})
        fair = collector._assess_fair({"identified": True}, metadata, False, False, True)
        assert fair["principles"]["Findable"] is False
        assert fair["principles"]["Interoperable"] is False

    def test_booktitle_is_not_title(self, collector):
        text = "@inproceedings{x,\n  booktitle = {Proc. SC}\n}"
        assert collector._analyze_bibtex(text, "o", "r")["present"] == []
