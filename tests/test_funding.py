"""Unit tests for FundingCollector (CASS Sections 4.2.8 and 4.2.9)."""

import pytest

from collectors.sustainability.funding import FundingCollector


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
        assert s["max_score"] == 5
        assert s["sub_scores"]["institutional_support"]["passing"]
        assert s["score"] <= 5


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
        from collectors.sustainability.funding import _GRANT_PATTERNS
        hit = any(re.search(p, text, re.IGNORECASE) for p, _ in _GRANT_PATTERNS)
        assert hit is expected
