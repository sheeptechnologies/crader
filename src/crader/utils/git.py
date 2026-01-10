import subprocess
from typing import List, Optional


class GitClient:
    def __init__(self, repo_path: str):
        self.repo_path = repo_path

    def _run_git(self, args: List[str]) -> str:
        try:
            return subprocess.check_output(
                ["git"] + args, cwd=self.repo_path, text=True, stderr=subprocess.DEVNULL
            ).strip()
        except subprocess.CalledProcessError:
            return ""

    def get_remote_url(self) -> Optional[str]:
        return self._run_git(["config", "--get", "remote.origin.url"]) or None

    def get_current_commit(self) -> str:
        return self._run_git(["rev-parse", "HEAD"]) or "unknown"

    def get_current_branch(self) -> str:
        return self._run_git(["rev-parse", "--abbrev-ref", "HEAD"]) or "unknown"

    def get_changed_files(self, since_commit: str) -> List[str]:
        if not since_commit or since_commit == "unknown":
            return []
        try:
            output = subprocess.check_output(
                ["git", "diff", "--name-only", since_commit, "HEAD"],
                cwd=self.repo_path,
                text=True,
                stderr=subprocess.PIPE,
            )
            return [f.strip() for f in output.splitlines() if f.strip()]
        except subprocess.CalledProcessError:
            return []
