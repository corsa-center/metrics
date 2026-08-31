"""Unit tests for the 4.2.1 additions to CommunityHealthCollector."""

import pytest

from collectors.sustainability.community_health import CommunityHealthCollector


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
        out = collector._match_pattern(index, collector.CONTRIBUTING_PATTERNS)
        assert out["exists"] is True
        assert out["file_path"] == "Contributing.md"

    def test_screaming_case_still_found(self, collector):
        index = {"contributing.md": _entry("CONTRIBUTING.md")}
        assert collector._match_pattern(index, collector.CONTRIBUTING_PATTERNS)["exists"]

    def test_nested_path_found(self, collector):
        index = {".github/code_of_conduct.md":
                 _entry("CODE_OF_CONDUCT.md", ".github/CODE_OF_CONDUCT.md")}
        assert collector._match_pattern(index, collector.COC_PATTERNS)["exists"]

    def test_absent_document(self, collector):
        out = collector._match_pattern({}, collector.GOVERNANCE_PATTERNS)
        assert out["exists"] is False
        assert out["file_path"] is None

    def test_first_matching_pattern_wins(self, collector):
        index = {"governance.md": _entry("GOVERNANCE.md"),
                 "docs/governance.md": _entry("governance.md", "docs/governance.md")}
        out = collector._match_pattern(index, collector.GOVERNANCE_PATTERNS)
        assert out["file_path"] == "GOVERNANCE.md"


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
