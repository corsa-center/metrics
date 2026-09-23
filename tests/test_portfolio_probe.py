"""Unit tests for tools/portfolio_probe.py's result classification.

Only _report is tested here -- everything else in the tool is a thin,
network-driven wrapper around already-tested collector code (RepoTree,
ReproducibilityCollector._check_semantic_versioning,
MetricsOrchestrator._confirm_repo_exists), by design (see the module
docstring): reusing that logic rather than re-deriving it is the point,
so there's nothing new to unit-test there beyond what those modules'
own test suites already cover.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.portfolio_probe import _report


def _ok(repo, **extra):
    return {"repo": repo, "exists": True, **extra}


def _missing(repo):
    return {"repo": repo, "exists": False}


class TestReport:
    def test_all_resolving_repos_is_a_clean_pass(self, capsys):
        code = _report([_ok("HDFGroup/hdf5"), _ok("AMReX-Codes/amrex")])
        assert code == 0

    def test_unresolved_catalog_entry_is_a_hard_failure(self, capsys):
        code = _report([_ok("HDFGroup/hdf5"), _missing("vtk/vtk")])
        assert code == 1
        out = capsys.readouterr().out
        assert "vtk/vtk" in out
        assert "DO NOT RESOLVE" in out

    def test_partial_workflow_scan_is_reported_not_a_failure(self, capsys):
        code = _report([_ok("HDFGroup/hdf5", workflow_count=76, workflow_scan_partial=True)])
        assert code == 0
        out = capsys.readouterr().out
        assert "PARTIAL" in out
        assert "76 workflows" in out

    def test_no_version_scheme_is_reported_not_a_failure(self, capsys):
        code = _report([_ok("sandialabs/Albany", version_scheme_found=False, tags_sampled=[])])
        assert code == 0
        out = capsys.readouterr().out
        assert "NO RECOGNIZED VERSION SCHEME" in out
        assert "sandialabs/Albany" in out

    def test_found_version_scheme_is_not_reported(self, capsys):
        code = _report([_ok("AMReX-Codes/amrex", version_scheme_found=True, tags_sampled=["26.09"])])
        assert code == 0
        out = capsys.readouterr().out
        assert "NO RECOGNIZED VERSION SCHEME" not in out

    def test_transient_gap_is_reported_not_a_failure(self, capsys):
        code = _report([{"repo": "LLNL/RAJA", "exists": True, "tree_gap": True}])
        assert code == 0
        out = capsys.readouterr().out
        assert "COULD NOT FETCH" in out

    def test_missing_repo_takes_priority_over_a_clean_report(self, capsys):
        # A hard failure must still be reported even alongside otherwise
        # clean results, and the exit code reflects the failure.
        code = _report([_ok("HDFGroup/hdf5"), _missing("paraview/paraview")])
        assert code == 1

    def test_empty_results(self, capsys):
        assert _report([]) == 0
