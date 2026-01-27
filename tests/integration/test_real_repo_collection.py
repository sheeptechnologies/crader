import os
import shutil
import subprocess
import pytest
from crader.collector.collector import SourceCollector

@pytest.mark.slow
def test_collect_real_flask_repo(tmp_path):
    """
    Integration test that clones a real repository (Flask) and verifies
    that the SourceCollector can handle a real-world file structure.
    
    We use a specific tag to ensure deterministic results.
    """
    repo_url = "https://github.com/pallets/flask.git"
    # tagging 2.3.3 for stability
    repo_tag = "2.3.3" 
    repo_dir = tmp_path / "flask_repo"
    
    # 1. Clone the repo
    print(f"Cloning {repo_url} into {repo_dir}...")
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", repo_tag, repo_url, str(repo_dir)],
        check=True,
        capture_output=True # Don't spam stdout
    )

    # 2. Run Collector
    collector = SourceCollector(str(repo_dir))
    all_files = [f for batch in collector.stream_files() for f in batch]
    
    # 3. Verify Statistics
    total_count = len(all_files)
    print(f"Collected {total_count} files from Flask {repo_tag}")
    
    # Flask 2.3.3 should have roughly 30-50 files (excluding excluded ones)
    # Use loose bounds to differ from future gitignore changes, but ensure not empty.
    assert total_count > 10, "Should collect at least 10 files"
    assert total_count < 1000, "Should not collect node_modules or massive generated stuff"

    # 4. Verify Specific Files existence and categorization
    # Map rel_path -> CollectedFile for easy lookup
    file_map = {f.rel_path: f for f in all_files}
    
    # Check core source file
    app_py = file_map.get("src/flask/app.py")
    assert app_py is not None, "src/flask/app.py not found"
    assert app_py.category == "source"
    assert app_py.extension == ".py"
    assert app_py.is_tracked is True
    
    # Check config file
    pyproject = file_map.get("pyproject.toml")
    assert pyproject is not None, "pyproject.toml not found"
    assert pyproject.category == "config"
    
    # Check test file (if present in the distribution/repo structure)
    # Flask repo usually has tests at root level 'tests/'
    # We just pick one valid test file we expect to exist.
    # Note: Flask's structure might vary, let's find ANY test file
    test_files = [f for f in all_files if f.category == "test"]
    assert len(test_files) > 0, "No test files identified"
    
    # 5. Check blocked directories are respected
    # Ensure .git/ is not collected
    git_files = [f for f in all_files if ".git/" in f.rel_path]
    assert len(git_files) == 0, ".git directory should be ignored"
