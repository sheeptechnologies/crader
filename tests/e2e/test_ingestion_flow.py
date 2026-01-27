import pytest
from crader.collector.collector import SourceCollector

class TestIngestionFlow:
    """
    End-to-End tests simulating the SourceCollector usage in the indexing pipeline.
    """

    def test_chunking_and_data_integrity(self, temp_git_repo, create_file_helper):
        """
        Scenario:
        A repo with multiple file types and mixed states.
        We verify the chunking mechanism and the integrity of the output objects.
        """
        # 1. Setup Repo Structure
        files_to_create = {
            "src/main.py": "source",
            "src/utils.py": "source",
            "tests/test_main.py": "test",
            "docs/README.md": "docs",
            "package.json": "config"
        }

        for path, _ in files_to_create.items():
            create_file_helper(temp_git_repo, path, "content")

        # Commit everything
        import subprocess
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "setup"], cwd=temp_git_repo, check=True)

        # 2. Run Collector with small chunk size
        chunk_size = 2
        collector = SourceCollector(str(temp_git_repo))
        
        batches = list(collector.stream_files(chunk_size=chunk_size))
        
        # 3. Assertions
        total_files = sum(len(b) for b in batches)
        assert total_files == 5, "All files should be collected"
        
        # Verify chunks (5 files / 2 = 2 batches of 2 + 1 batch of 1)
        assert len(batches) == 3 
        assert len(batches[0]) == 2
        assert len(batches[1]) == 2
        assert len(batches[2]) == 1

        # 4. Verify Data Integrity for a sample file
        all_files = [f for batch in batches for f in batch]
        readme = next(f for f in all_files if f.rel_path == "docs/README.md")
        
        assert readme.category == "docs"
        assert readme.extension == ".md"
        assert readme.is_tracked is True
        assert readme.full_path == str(temp_git_repo / "docs/README.md")
        assert readme.size_bytes > 0

    def test_empty_repo_handling(self, temp_git_repo):
        """
        Scenario: An empty initialized repo.
        Should yield nothing and not crash.
        """
        collector = SourceCollector(str(temp_git_repo))
        batches = list(collector.stream_files())
        assert len(batches) == 0

    def test_binary_garbage_handling(self, temp_git_repo, create_file_helper):
        """
        Scenario: Repo contains binary files (images) and unsupported scripts.
        """
        create_file_helper(temp_git_repo, "image.png", "binary_data")
        create_file_helper(temp_git_repo, "script.sh", "echo hi") # Assuming .sh is allowed
        create_file_helper(temp_git_repo, "unknown.xyz", "???")   # Unsupported extension

        import subprocess
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
        
        collector = SourceCollector(str(temp_git_repo))
        # Ensure .xyz is not in supported extensions for this test context
        # (Assuming default config)
        
        files = [f.rel_path for batch in collector.stream_files() for f in batch]
        
        assert "image.png" not in files
        assert "unknown.xyz" not in files
        # Check config to see if .sh is supported. If not, assert not in files.