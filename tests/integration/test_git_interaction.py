import os
import subprocess
import pytest
from crader.collector.collector import SourceCollector

class TestGitInteraction:
    """
    Integration tests focusing on Git commands and Filesystem interactions.
    """

    def test_respects_gitignore(self, temp_git_repo, create_file_helper):
        """
        Git ls-files must strictly respect .gitignore rules.
        """
        create_file_helper(temp_git_repo, ".gitignore", "*.secret\n/logs")
        create_file_helper(temp_git_repo, "api_key.secret", "12345")
        create_file_helper(temp_git_repo, "logs/app.log", "error")
        create_file_helper(temp_git_repo, "src/main.py", "print('ok')")

        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=temp_git_repo, check=True)

        collector = SourceCollector(str(temp_git_repo))
        files = [f.rel_path for batch in collector.stream_files() for f in batch]

        assert "src/main.py" in files
        assert "api_key.secret" not in files
        assert "logs/app.log" not in files

    def test_safety_filters_symlinks_and_blocklist(self, temp_git_repo, create_file_helper):
        """
        Ensures safety mechanisms (Symlink rejection, Blocklist enforcement) work 
        even if Git tracks the files.
        """
        # 1. Blocklist Violation (node_modules committed by mistake)
        create_file_helper(temp_git_repo, "node_modules/lib/index.js", "bad")
        
        # 2. Symlink creation
        create_file_helper(temp_git_repo, "real_file.py", "print('hi')")
        os.symlink("real_file.py", str(temp_git_repo / "link_to_file.py"))

        # Force add ignored/special files
        subprocess.run(["git", "add", "-f", "."], cwd=temp_git_repo, check=True)
        
        collector = SourceCollector(str(temp_git_repo))
        files = [f.rel_path for batch in collector.stream_files() for f in batch]

        assert "real_file.py" in files
        assert "link_to_file.py" not in files  # Symlinks rejected
        assert "node_modules/lib/index.js" not in files  # Blocklist rejected

    def test_detects_untracked_files(self, temp_git_repo, create_file_helper):
        """
        Verifies that files present in the workspace but not in the index 
        are collected (but without a hash).
        """
        create_file_helper(temp_git_repo, "committed.py", "print(1)")
        subprocess.run(["git", "add", "committed.py"], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "1"], cwd=temp_git_repo, check=True)

        # Create a new file (Untracked)
        create_file_helper(temp_git_repo, "wip_feature.py", "print(2)")

        collector = SourceCollector(str(temp_git_repo))
        results = [f for batch in collector.stream_files() for f in batch]

        committed = next(f for f in results if f.rel_path == "committed.py")
        untracked = next(f for f in results if f.rel_path == "wip_feature.py")

        assert committed.git_hash is not None
        assert untracked.git_hash is None
        assert untracked.category == "source"

    def test_size_limit_enforcement(self, temp_git_repo, create_file_helper):
        """
        Verifies that files exceeding the size limit are dropped.
        """
        collector = SourceCollector(str(temp_git_repo))
        collector.max_size = 50  # Set tiny limit for testing

        create_file_helper(temp_git_repo, "small.py", "x" * 10)
        create_file_helper(temp_git_repo, "huge.py", "x" * 100)

        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
        
        files = [f.rel_path for batch in collector.stream_files() for f in batch]
        
        assert "small.py" in files
        assert "huge.py" not in files