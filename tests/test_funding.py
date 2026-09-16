"""Unit tests for FundingCollector (CASS Sections 4.2.8 and 4.2.9)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from collectors.ecosystem.base import COLLECTION_GAP
from collectors.ecosystem.funding import FundingCollector, _FUNDING_FILES


@pytest.fixture
def collector():
    return FundingCollector()


class TestCompanyNormalization:
    def test_splits_multiple_employers(self, collector):
        assert collector._normalize_companies("The HDFgroup, CGNS") == [
            "The HDFgroup", "CGNS"
        ]

    def test_strips_github_handle_marker(self, collector):
        assert collector._normalize_companies("@HDFGroup") == ["HDFGroup"]

    def test_drops_non_affiliations(self, collector):
        assert collector._normalize_companies("independent") == []
        assert collector._normalize_companies("-, none, N/A") == []

    @pytest.mark.parametrize("a,b", [
        ("The HDF Group", "HDFGroup"),
        ("The HDFgroup", "hdf group"),
        ("Lawrence Livermore", "lawrence-livermore"),
    ])
    def test_spelling_variants_fold_together(self, collector, a, b):
        assert collector._canonical_org(a) == collector._canonical_org(b)

    def test_distinct_orgs_stay_distinct(self, collector):
        assert collector._canonical_org("AMD") != collector._canonical_org("CGNS")


class TestScoring:
    def _score(self, collector, files=None, grants=None, affil=None, owner_type=None):
        return collector._calculate_score(
            files or {"found": [], "platforms": []},
            grants or [],
            affil or {"organizations": [], "sampled": 0, "with_affiliation": 0},
            owner_type,
        )

    def test_funding_documentation_passes_on_file_alone(self, collector):
        s = self._score(collector, files={"found": [{"path": ".github/FUNDING.yml"}],
                                          "platforms": ["github"]})
        assert s["sub_scores"]["funding_documentation"]["passing"]

    def test_funding_documentation_passes_on_grant_alone(self, collector):
        s = self._score(collector, grants=[{"value": "DE-AC02-06CH11357", "kind": "DOE"}])
        assert s["sub_scores"]["funding_documentation"]["passing"]

    def test_no_funding_signals_fails(self, collector):
        assert not self._score(collector)["sub_scores"]["funding_documentation"]["passing"]

    def test_portfolio_counts_platforms_and_grants(self, collector):
        s = self._score(collector,
                        files={"found": [], "platforms": ["github"]},
                        grants=[{"value": "OAC-1234567", "kind": "NSF"}])
        assert "2 distinct" in s["sub_scores"]["funding_portfolio"]["value"]
        assert s["sub_scores"]["funding_portfolio"]["passing"]

    def test_single_source_fails_portfolio(self, collector):
        s = self._score(collector, files={"found": [], "platforms": ["github"]})
        assert not s["sub_scores"]["funding_portfolio"]["passing"]

    def test_org_ownership_counts_as_corporate_signal(self, collector):
        s = self._score(collector, owner_type="Organization")
        assert s["sub_scores"]["corporate_sponsorship"]["passing"]
        s = self._score(collector, owner_type="User")
        assert not s["sub_scores"]["corporate_sponsorship"]["passing"]

    def test_affiliation_threshold(self, collector):
        two = {"organizations": [{"name": "a", "contributors": 1},
                                 {"name": "b", "contributors": 1}],
               "sampled": 5, "with_affiliation": 2}
        assert not self._score(collector, affil=two)["sub_scores"][
            "institutional_affiliation"]["passing"]
        three = dict(two)
        three["organizations"] = two["organizations"] + [{"name": "c", "contributors": 1}]
        assert self._score(collector, affil=three)["sub_scores"][
            "institutional_affiliation"]["passing"]

    def test_nih_stays_uncollected(self, collector):
        assert self._score(collector)["sub_scores"]["nih_r50"]["not_collected"]

    def test_score_covers_only_the_four_two_eight_rows(self, collector):
        # institutional_support belongs to 4.2.9 and must not inflate 4.2.8.
        s = self._score(collector, affil={"organizations": [{"name": n, "contributors": 1}
                                                            for n in "abc"],
                                          "sampled": 3, "with_affiliation": 3})
        # nih_r50 is permanently not_collected by design (no data source
        # exists for it) and must not inflate the denominator either --
        # only 4 of the 5 financial_keys are ever actually scorable.
        assert s["max_score"] == 4
        assert s["sub_scores"]["institutional_support"]["passing"]
        assert s["score"] <= 4


class TestScoringGapHandling:
    def test_no_funding_signals_under_gap_is_not_collected(self, collector):
        s = collector._calculate_score(
            {"found": [], "platforms": [], "not_collected": True}, [],
            {"organizations": [], "sampled": 0, "with_affiliation": 0}, None,
        )
        entry = s["sub_scores"]["funding_documentation"]
        assert entry["passing"] is False
        assert entry["not_collected"] is True

    def test_found_funding_file_survives_a_grant_gap(self, collector):
        s = collector._calculate_score(
            {"found": [{"path": "FUNDING.yml"}], "platforms": ["github"]}, [],
            {"organizations": [], "sampled": 0, "with_affiliation": 0}, None,
            grants_gap=True,
        )
        entry = s["sub_scores"]["funding_documentation"]
        assert entry["passing"] is True
        assert "not_collected" not in entry

    def test_affiliation_below_threshold_under_gap_is_not_collected(self, collector):
        s = collector._calculate_score(
            {"found": [], "platforms": []}, [],
            {"organizations": [], "sampled": 0, "with_affiliation": 0, "gap": True}, None,
        )
        entry = s["sub_scores"]["institutional_affiliation"]
        assert entry["not_collected"] is True
        # 4.2.9's row shares the same gap signal.
        assert s["sub_scores"]["institutional_support"]["not_collected"] is True

    def test_affiliation_threshold_already_met_survives_a_gap(self, collector):
        orgs = [{"name": n, "contributors": 1} for n in "abc"]
        s = collector._calculate_score(
            {"found": [], "platforms": []}, [],
            {"organizations": orgs, "sampled": 3, "with_affiliation": 3, "gap": True}, None,
        )
        entry = s["sub_scores"]["institutional_affiliation"]
        assert entry["passing"] is True
        assert "not_collected" not in entry

    def test_corporate_sponsorship_under_owner_type_gap_is_not_collected(self, collector):
        s = collector._calculate_score(
            {"found": [], "platforms": []}, [],
            {"organizations": [], "sampled": 0, "with_affiliation": 0}, None,
            owner_type_gap=True,
        )
        assert s["sub_scores"]["corporate_sponsorship"]["not_collected"] is True

    def test_org_ownership_signal_survives_a_files_gap(self, collector):
        s = collector._calculate_score(
            {"found": [], "platforms": [], "not_collected": True}, [],
            {"organizations": [], "sampled": 0, "with_affiliation": 0}, "Organization",
        )
        entry = s["sub_scores"]["corporate_sponsorship"]
        assert entry["passing"] is True
        assert "not_collected" not in entry

    def test_everything_gapped_reports_not_collected_status(self, collector):
        s = collector._calculate_score(
            {"found": [], "platforms": [], "not_collected": True}, [],
            {"organizations": [], "sampled": 0, "with_affiliation": 0, "gap": True}, None,
            grants_gap=True, owner_type_gap=True,
        )
        assert s["score"] is None
        assert s["max_score"] == 0
        assert s["status"] == "not_collected"


class TestFindFundingFilesGapHandling:
    def _run(self, collector, responses):
        async def fake_exists(client, owner, repo, path):
            return responses.get(path, None)

        async def go():
            with patch.object(collector, "_check_file_exists", side_effect=fake_exists):
                return await collector._find_funding_files(None, "o", "r")

        return asyncio.run(go())

    def test_gap_with_no_find_is_not_collected(self, collector):
        responses = {p: COLLECTION_GAP for p in _FUNDING_FILES}
        result = self._run(collector, responses)
        assert result["found"] == []
        assert result["not_collected"] is True

    def test_found_file_is_not_marked_not_collected_despite_gaps_elsewhere(self, collector):
        responses = {p: COLLECTION_GAP for p in _FUNDING_FILES}
        responses[".github/FUNDING.yml"] = "http://x"

        async def fake_exists(client, owner, repo, path):
            return responses.get(path, None)

        async def fake_platforms(client, owner, repo, path):
            return [], False

        async def go():
            with patch.object(collector, "_check_file_exists", side_effect=fake_exists), \
                 patch.object(collector, "_read_funding_platforms", side_effect=fake_platforms):
                return await collector._find_funding_files(None, "o", "r")

        result = asyncio.run(go())
        assert len(result["found"]) == 1
        assert "not_collected" not in result


class TestGetOwnerTypeGapHandling:
    def test_gap_is_tracked(self, collector):
        async def go():
            with patch.object(collector, "_github_get", new=AsyncMock(return_value=COLLECTION_GAP)):
                return await collector._get_owner_type(None, "o")

        owner_type, saw_gap = asyncio.run(go())
        assert owner_type is None
        assert saw_gap is True

    def test_confirmed_user_type_is_not_a_gap(self, collector):
        async def go():
            with patch.object(collector, "_github_get", new=AsyncMock(return_value={"type": "User"})):
                return await collector._get_owner_type(None, "o")

        owner_type, saw_gap = asyncio.run(go())
        assert owner_type == "User"
        assert saw_gap is False


class TestGetAffiliationsGapHandling:
    def test_contributors_listing_gap_is_tracked(self, collector):
        async def go():
            with patch.object(collector, "_github_get", new=AsyncMock(return_value=COLLECTION_GAP)):
                return await collector._get_affiliations(None, "o", "r")

        result = asyncio.run(go())
        assert result["gap"] is True
        assert result["sampled"] == 0

    def test_per_contributor_gap_excludes_from_sample(self, collector):
        contributors = [{"login": "alice"}, {"login": "bob"}]

        async def fake_github_get(client, url, params=None):
            if url.endswith("/contributors"):
                return contributors
            if url.endswith("/users/alice"):
                return {"company": "HDF Group"}
            if url.endswith("/users/bob"):
                return COLLECTION_GAP
            return None

        async def go():
            with patch.object(collector, "_github_get", side_effect=fake_github_get):
                return await collector._get_affiliations(None, "o", "r")

        result = asyncio.run(go())
        assert result["sampled"] == 1
        assert result["with_affiliation"] == 1
        assert result["gap"] is True


class TestGrantPatterns:
    @pytest.mark.parametrize("text,expected", [
        ("Supported by DE-AC02-06CH11357", True),
        ("under NSF OAC-1836650", True),
        ("award R01GM123456 funded", True),
        ("see version 1.14.3 and issue 12345", False),
        ("no funding here", False),
    ])
    def test_patterns(self, text, expected):
        import re
        from collectors.ecosystem.funding import _GRANT_PATTERNS
        hit = any(re.search(p, text, re.IGNORECASE) for p, _ in _GRANT_PATTERNS)
        assert hit is expected
