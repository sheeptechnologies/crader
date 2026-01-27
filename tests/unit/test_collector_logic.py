import pytest
from crader.collector.collector import SourceCollector
from crader.collector.schema import CollectedFile

class TestCollectorLogic:
    """
    Unit tests for SourceCollector internal logic.
    Focuses on classification heuristics and filtering rules.
    """

    def test_category_determination_heuristics(self):
        """
        Verifies that file paths are correctly mapped to semantic categories.
        """
        # We instantiate with specific path; no disk I/O occurs in this method.
        collector = SourceCollector("/dummy/path")
        
        # 1. Source Code
        assert collector._determine_category("src/main.py") == "source"
        assert collector._determine_category("utils/string_utils.js") == "source"
        assert collector._determine_category("app/components/Header.tsx") == "source"

        # 2. Tests
        assert collector._determine_category("tests/test_login.py") == "test"
        assert collector._determine_category("src/auth/login_test.go") == "test"
        assert collector._determine_category("spec/user.spec.ts") == "test"
        assert collector._determine_category("__tests__/utils.js") == "test"

        # 3. Configuration
        assert collector._determine_category("package.json") == "config"
        assert collector._determine_category("pyproject.toml") == "config"
        assert collector._determine_category("Dockerfile") == "config"
        assert collector._determine_category("ci/pipeline.yaml") == "config"

        # 4. Documentation
        assert collector._determine_category("README.md") == "docs"
        assert collector._determine_category("docs/api/v1.rst") == "docs"

    def test_collected_file_properties(self):
        """
        Verifies properties and behavior of the CollectedFile DTO.
        """
        # Case A: Tracked file (Has Hash)
        tracked_file = CollectedFile(
            rel_path="main.py",
            full_path="/tmp/main.py",
            extension=".py",
            size_bytes=100,
            git_hash="a1b2c3d4",
            category="source"
        )
        assert tracked_file.is_tracked is True

        # Case B: Untracked file (No Hash)
        untracked_file = CollectedFile(
            rel_path="new.py",
            full_path="/tmp/new.py",
            extension=".py",
            size_bytes=100,
            git_hash=None,
            category="source"
        )
        assert untracked_file.is_tracked is False

    def test_blocklist_logic(self):
        """
        Verifies that the blocklist logic correctly identifies blocked paths.
        Note: We test the internal logic by inspecting the blocklist set configuration.
        """
        collector = SourceCollector(".")
        blocked_dirs = collector.blocklist
        
        assert "node_modules" in blocked_dirs
        assert ".git" in blocked_dirs
        assert "venv" in blocked_dirs
        assert "dist" in blocked_dirs