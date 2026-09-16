"""Unit tests for the 4.2.1 additions to CommunityHealthCollector."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from collectors.ecosystem.community_health import CommunityHealthCollector


@pytest.fixture
def collector():
    return CommunityHealthCollector()


def _entry(name, path=None):
    return {"name": name, "path": path or name, "type": "file",
            "html_url": f"https://github.com/x/y/blob/HEAD/{path or name}", "size": 100}


class TestCaseInsensitiveDetection:
    def test_title_case_filename_is_found(self, collector):
        # ADIOS2 names its guide Contributing.md; the Contents API is
        # case-sensitive, so an enumerated pattern list missed it entirely.
        index = {"contributing.md": _entry("Contributing.md")}
        out = collector._match_pattern(index, collector.CONTRIBUTING_PATTERNS, "o", "r")
        assert out["exists"] is True
        assert out["file_path"] == "Contributing.md"

    def test_screaming_case_still_found(self, collector):
        index = {"contributing.md": _entry("CONTRIBUTING.md")}
        assert collector._match_pattern(index, collector.CONTRIBUTING_PATTERNS, "o", "r")["exists"]

    def test_nested_path_found(self, collector):
        index = {".github/code_of_conduct.md":
                 _entry("CODE_OF_CONDUCT.md", ".github/CODE_OF_CONDUCT.md")}
        assert collector._match_pattern(index, collector.COC_PATTERNS, "o", "r")["exists"]

    def test_absent_document(self, collector):
        out = collector._match_pattern({}, collector.GOVERNANCE_PATTERNS, "o", "r")
        assert out["exists"] is False
        assert out["file_path"] is None

    def test_first_matching_pattern_wins(self, collector):
        index = {"governance.md": _entry("GOVERNANCE.md"),
                 "docs/governance.md": _entry("governance.md", "docs/governance.md")}
        out = collector._match_pattern(index, collector.GOVERNANCE_PATTERNS, "o", "r")
        assert out["file_path"] == "GOVERNANCE.md"

    def test_match_records_which_repo_it_came_from(self, collector):
        index = {"governance.md": _entry("GOVERNANCE.md")}
        out = collector._match_pattern(index, collector.GOVERNANCE_PATTERNS, "kokkos", "governance")
        assert out["repository"] == "kokkos/governance"


class TestKeywordGroups:
    def _groups(self, collector, text):
        corpus = text.lower()
        return [g for g, terms in collector.GOVERNANCE_KEYWORDS.items()
                if any(t in corpus for t in terms)]

    def test_decision_process(self, collector):
        assert "Decision process" in self._groups(
            collector, "Changes are approved by consensus of the maintainers.")

    def test_roles(self, collector):
        assert "Defined roles" in self._groups(
            collector, "The technical committee reviews all proposals.")

    def test_membership_lifecycle(self, collector):
        assert "Membership lifecycle" in self._groups(
            collector, "Nomination of new committers happens quarterly.")

    def test_conflict_resolution(self, collector):
        assert "Conflict resolution" in self._groups(
            collector, "Disputes are escalated to the steering group.")

    def test_bare_document_matches_nothing(self, collector):
        assert self._groups(collector, "Thanks for your interest in the project.") == []


class TestStaleness:
    def test_three_year_boundary(self, collector):
        assert collector.GOVERNANCE_STALE_DAYS == 1095


def _found(path):
    return {"exists": True, "file_path": path, "url": f"https://github.com/x/y/blob/HEAD/{path}",
            "size": 10, "content_preview": "", "repository": "x/y"}


def _not_found():
    return {"exists": False, "file_path": None, "url": None, "repository": None}


class TestFallbackRepos:
    """Kokkos is the confirmed real case: kokkos/kokkos has no GOVERNANCE.md
    and no link anywhere to kokkos/governance (checked live via GitHub code
    search), but kokkos/governance itself has GOVERNANCE.md,
    code-of-conduct.md, and a technical charter.
    """

    def test_nothing_missing_skips_fallback_entirely(self, collector):
        coc, gov, contrib = _found("CODE_OF_CONDUCT.md"), _found("GOVERNANCE.md"), _found("CONTRIBUTING.md")
        with patch.object(collector, "_build_file_index", new=AsyncMock()) as mock_index:
            result = asyncio.run(collector._check_fallback_repos("o", coc, gov, contrib))
        mock_index.assert_not_called()
        assert result == (coc, gov, contrib)

    def test_missing_governance_found_in_first_fallback(self, collector):
        coc, contrib = _found("CODE_OF_CONDUCT.md"), _found("CONTRIBUTING.md")
        gov = _not_found()

        async def fake_index(owner, repo):
            if repo == "governance":
                return {"governance.md": {"path": "GOVERNANCE.md", "html_url": "http://x", "size": 1}}
            return {}

        with patch.object(collector, "_build_file_index", side_effect=fake_index):
            result_coc, result_gov, result_contrib = asyncio.run(
                collector._check_fallback_repos("kokkos", coc, gov, contrib)
            )
        assert result_gov["exists"] is True
        assert result_gov["repository"] == "kokkos/governance"
        # Untouched entries pass through unchanged.
        assert result_coc is coc
        assert result_contrib is contrib

    def test_falls_through_to_second_fallback_repo(self, collector):
        gov = _not_found()
        calls = []

        async def fake_index(owner, repo):
            calls.append(repo)
            if repo == ".github":
                return {"governance.md": {"path": "GOVERNANCE.md", "html_url": "http://x", "size": 1}}
            return {}

        with patch.object(collector, "_build_file_index", side_effect=fake_index):
            _, result_gov, _ = asyncio.run(
                collector._check_fallback_repos("o", _not_found(), gov, _not_found())
            )
        assert calls == ["governance", ".github"]
        assert result_gov["exists"] is True
        assert result_gov["repository"] == "o/.github"

    def test_stops_once_everything_is_found(self, collector):
        calls = []

        async def fake_index(owner, repo):
            calls.append(repo)
            return {
                "governance.md": {"path": "GOVERNANCE.md", "html_url": "http://x", "size": 1},
                "code_of_conduct.md": {"path": "CODE_OF_CONDUCT.md", "html_url": "http://y", "size": 1},
            }

        with patch.object(collector, "_build_file_index", side_effect=fake_index):
            asyncio.run(
                collector._check_fallback_repos("o", _not_found(), _not_found(), _found("CONTRIBUTING.md"))
            )
        assert calls == ["governance"]  # never needed to try .github

    def test_nonexistent_fallback_repo_does_not_crash(self, collector):
        with patch.object(collector, "_build_file_index", new=AsyncMock(return_value={})):
            result = asyncio.run(
                collector._check_fallback_repos("o", _not_found(), _not_found(), _not_found())
            )
        assert all(r["exists"] is False for r in result)
