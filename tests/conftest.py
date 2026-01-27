import sys
import pytest
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """
    Creates a temporary, initialized Git repository.
    
    This fixture:
    1. Creates a directory.
    2. Initializes git.
    3. Configures a dummy user (required for commits).
    
    Returns:
        Path: The absolute path to the repository root.
    """
    repo_root = tmp_path / "test_repo"
    repo_root.mkdir()
    
    # Initialize Git
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
    
    # Configure dummy user to allow commits in CI environments
    subprocess.run(["git", "config", "user.email", "bot@example.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "Test Bot"], cwd=repo_root, check=True)
    
    return repo_root

@pytest.fixture
def create_file_helper():
    """
    Returns a function to create files within the test repo easily.
    Handles directory creation automatically.
    """
    def _create(repo_root: Path, rel_path: str, content: str = "content") -> Path:
        full_path = repo_root / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        return full_path
    return _create