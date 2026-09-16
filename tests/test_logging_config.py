"""Unit tests for MetricsOrchestrator._configure_logging.

config/orchestrator.yaml has declared logging.file: "orchestrator.log"
since before this session, but logging.basicConfig() at module import time
ran before any config was loaded, hardcoded to stdout-only -- so the file
was never actually written. Without a persisted log, "why did this metric
come back empty" could only be answered by reproducing the collector
locally by hand, which is how every gap in the 2026-09-16 incident
actually got diagnosed.
"""

import logging
import os

import pytest

from orchestrator import MetricsOrchestrator


@pytest.fixture
def orch():
    return MetricsOrchestrator.__new__(MetricsOrchestrator)


@pytest.fixture(autouse=True)
def _clean_root_handlers():
    """Root logger is process-global; don't let one test's FileHandler
    leak into the next."""
    root = logging.getLogger()
    before = list(root.handlers)
    yield
    for h in list(root.handlers):
        if h not in before:
            root.removeHandler(h)
            h.close()


class TestConfigureLogging:
    def test_adds_a_file_handler_for_the_configured_file(self, orch, tmp_path):
        log_file = tmp_path / "orchestrator.log"
        orch.config = {"logging": {"file": str(log_file), "level": "INFO"}}
        orch._configure_logging()

        root = logging.getLogger()
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        assert any(h.baseFilename == os.path.abspath(str(log_file)) for h in file_handlers)

    def test_warning_actually_lands_in_the_file(self, orch, tmp_path):
        log_file = tmp_path / "orchestrator.log"
        orch.config = {"logging": {"file": str(log_file), "level": "INFO"}}
        orch._configure_logging()

        logging.getLogger("collectors.ecosystem.base").warning(
            "COLLECTION-GAP url=https://api.github.com/repos/o/r status=403 reason=test"
        )
        for h in logging.getLogger().handlers:
            h.flush()

        content = log_file.read_text()
        assert "COLLECTION-GAP" in content

    def test_does_not_add_a_duplicate_handler_on_repeat_calls(self, orch, tmp_path):
        log_file = tmp_path / "orchestrator.log"
        orch.config = {"logging": {"file": str(log_file), "level": "INFO"}}
        orch._configure_logging()
        orch._configure_logging()

        root = logging.getLogger()
        matching = [
            h for h in root.handlers
            if isinstance(h, logging.FileHandler) and h.baseFilename == os.path.abspath(str(log_file))
        ]
        assert len(matching) == 1

    def test_level_defaults_to_info_when_unset(self, orch):
        orch.config = {}
        orch._configure_logging()
        assert logging.getLogger().level == logging.INFO

    def test_level_is_read_from_config(self, orch):
        orch.config = {"logging": {"level": "DEBUG"}}
        orch._configure_logging()
        assert logging.getLogger().level == logging.DEBUG

    def test_no_file_configured_adds_no_file_handler(self, orch):
        before = [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]
        orch.config = {"logging": {}}
        orch._configure_logging()
        after = [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]
        assert after == before
