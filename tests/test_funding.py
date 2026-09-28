"""Unit tests for FundingCollector (CASS Sections 4.2.8 and 4.2.9)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from collectors.ecosystem.base import COLLECTION_GAP, RepoTree
from collectors.ecosystem.funding import FundingCollector


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

    def test_acknowledgment_alone_documents_funding(self, collector):
        s = self._score(collector, grants=[{"value": "DOE", "kind": "acknowledgment"}])
        row = s["sub_scores"]["funding_documentation"]
        assert row["passing"]
        assert row["value"] == "funding acknowledged: DOE"
        assert row["detail"] is None

    def test_acknowledged_agency_already_covered_by_an_award_counts_once(self, collector):
        s = self._score(collector, grants=[
            {"value": "DE-SC0021354", "kind": "DOE award"},
            {"value": "DOE", "kind": "acknowledgment"},
        ])
        assert "1 distinct" in s["sub_scores"]["funding_portfolio"]["value"]

    def test_distinct_acknowledged_agencies_count_separately(self, collector):
        s = self._score(collector, grants=[
            {"value": "DE-SC0021354", "kind": "DOE award"},
            {"value": "NSF", "kind": "acknowledgment"},
        ])
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


class TestFindFundingFiles:
    """_find_funding_files now takes a RepoTree (or COLLECTION_GAP) directly
    -- see METRIC_BLIND_SPOTS.md class F1.
    """

    def test_gapped_tree_is_not_collected(self, collector):
        result = asyncio.run(collector._find_funding_files(None, "o", "r", COLLECTION_GAP))
        assert result["found"] == []
        assert result["not_collected"] is True

    def test_found_file_is_not_marked_not_collected(self, collector):
        tree = RepoTree("o", "r", [".github/FUNDING.yml"], truncated=False)

        async def fake_platforms(client, owner, repo, path):
            return [], False

        async def go():
            with patch.object(collector, "_read_funding_platforms", side_effect=fake_platforms):
                return await collector._find_funding_files(None, "o", "r", tree)

        result = asyncio.run(go())
        assert len(result["found"]) == 1
        assert "not_collected" not in result

    def test_confirmed_absence_is_not_collected_free(self, collector):
        tree = RepoTree("o", "r", ["README.md"], truncated=False)
        result = asyncio.run(collector._find_funding_files(None, "o", "r", tree))
        assert result["found"] == []
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
        ("DOE award DE-SC0021354", True),
        ("DOE awards DE-AC52-07NA27344 and DE-SC-0021354.", True),
        ("see version 1.14.3 and issue 12345", False),
        ("no funding here", False),
    ])
    def test_patterns(self, text, expected):
        import re
        from collectors.ecosystem.funding import _GRANT_PATTERNS
        hit = any(re.search(p, text, re.IGNORECASE) for p, _ in _GRANT_PATTERNS)
        assert hit is expected


class TestFindGrantReferences:
    def _grants(self, collector, readme):
        import base64
        from unittest.mock import AsyncMock, MagicMock, patch
        data = {"content": base64.b64encode(readme.encode()).decode()}

        async def go():
            with patch.object(collector, "_github_get", new=AsyncMock(return_value=data)):
                return await collector._find_grant_references(MagicMock(), "o", "r")
        grants, gap = asyncio.run(go())
        return [g["value"] for g in grants]

    def test_hyphenated_doe_award_counted_separately_from_contract(self, collector):
        readme = "under DOE awards DE-AC52-07NA27344 and DE-SC-0021354."
        assert self._grants(collector, readme) == ["DE-AC52-07NA27344", "DE-SC-0021354"]

    def test_same_award_written_two_ways_counts_once(self, collector):
        readme = "Supported by DE-SC0021354. See also award DE-SC-0021354."
        assert len(self._grants(collector, readme)) == 1


class TestAcknowledgedAgencies:
    @pytest.mark.parametrize("text,expected", [
        # AMReX's NOTICE: "U.S." must not end the sentence early.
        ("This Software was developed under funding from the U.S. Department\nof Energy.", ["DOE"]),
        ("This work was supported by the National Science Foundation and NASA.", ["NSF", "NASA"]),
        ("Funded in part by the Exascale Computing Project (17-SC-20-SC).", ["DOE"]),
        # Named, but not as a funder.
        ("AMReX is deployed on DOE HPC systems.", []),
        ("AMReX supports several Exascale Computing Project applications.", []),
        ("Funding for travel is available. Later, unrelated: see the NSF site.", []),
    ])
    def test_agencies(self, collector, text, expected):
        assert collector._acknowledged_agencies(text) == expected


class TestAcknowledgmentFiles:
    def test_notice_file_is_read_alongside_the_readme(self, collector):
        import base64
        from unittest.mock import MagicMock
        from collectors.ecosystem.base import RepoTree

        def enc(t):
            return {"content": base64.b64encode(t.encode()).decode()}

        responses = {
            "/readme": enc("AMReX is deployed on DOE HPC systems."),
            "/contents/NOTICE": enc("developed under funding from the U.S. Department of Energy"),
        }

        async def fake_get(client, url, params=None):
            for key, value in responses.items():
                if url.endswith(key):
                    return value
            return None

        collector._github_get = fake_get
        tree = RepoTree("o", "r", ["NOTICE", "docs/NOTICE"], truncated=False)
        grants, gap = asyncio.run(collector._find_grant_references(MagicMock(), "o", "r", tree))
        assert grants == [{"value": "DOE", "kind": "acknowledgment"}]
        assert gap is False

    def test_gapped_tree_is_a_gap(self, collector):
        from unittest.mock import MagicMock
        from collectors.ecosystem.base import COLLECTION_GAP

        async def fake_get(client, url, params=None):
            return None

        collector._github_get = fake_get
        _, gap = asyncio.run(collector._find_grant_references(MagicMock(), "o", "r", COLLECTION_GAP))
        assert gap is True
