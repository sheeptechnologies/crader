import contextlib
import fcntl
import hashlib
import logging
import os
import shutil
import subprocess
import uuid

# [OTEL] Import Trace API
from opentelemetry import trace

from ..config import STORAGE_ROOT

logger = logging.getLogger(__name__)

# [OTEL] Inizializzazione Tracer
tracer = trace.get_tracer(__name__)


class GitVolumeManager:
    """
    Manages the lifecycle of Git repositories on the local filesystem.

    This class provides precise control over cloning, fetching, and clean-up of Git repositories.
    It is heavily instrumented with OpenTelemetry to track I/O bottlenecks and lock contention.

    **Key Responsibilities:**
    *   **Persistent Cache**: Maintains bare mirrors of repositories to speed up subsequent operations (`_get_repo_cache_path`).
    *   **Concurrency Control**: Uses `fcntl` file locking to prevent race conditions between workers accessing the same repo.
    *   **Worktree Isolation**: Creates ephemeral git worktrees for read-only parsing, allowing parallel indexing of different commits without checking out files in the main repo.
    *   **Garbage Collection**: Automatically prunes stale worktrees and zombies.
    """

    def __init__(self):
        self.base_path = STORAGE_ROOT
        self.cache_dir = os.path.join(self.base_path, "cache")
        self.workspaces_dir = os.path.join(self.base_path, "workspaces")

        os.makedirs(self.cache_dir, exist_ok=True)
        os.makedirs(self.workspaces_dir, exist_ok=True)

    def _get_repo_cache_path(self, url: str) -> str:
        safe_name = hashlib.sha256(url.encode()).hexdigest()
        return os.path.join(self.cache_dir, f"{safe_name}.git")

    def ensure_repo_updated(self, url: str) -> str:
        """
        Synchronizes the local bare repository cache with the remote origin.

        This method is thread/process-safe via file locking.
        1.  Acquires an exclusive lock on the repo path.
        2.  If the repo is missing, performs `git clone --mirror`.
        3.  If it exists, performs `git fetch --all --prune`.
        4.  Releases the lock.

        Args:
            url (str): The Git remote URL.

        Returns:
            str: The absolute path to the local bare repository.
        """
        repo_path = self._get_repo_cache_path(url)
        lock_file = f"{repo_path}.lock"

        # [OTEL] Span principale per l'operazione di sync
        with tracer.start_as_current_span("git.ensure_updated") as span:
            span.set_attribute("repo.url", url)
            span.set_attribute("repo.path", repo_path)

            with open(lock_file, "w") as f:
                try:
                    # [OTEL] Misuriamo ESPLICITAMENTE l'attesa del lock
                    # Se questo tempo è alto, hai troppa concorrenza sulla stessa repo
                    with tracer.start_as_current_span("git.lock_wait") as lock_span:
                        lock_span.set_attribute("lock.file", lock_file)
                        fcntl.flock(f, fcntl.LOCK_EX)

                    # 2. CHECK & EXECUTE
                    # [OTEL] Misuriamo l'esecuzione del comando git (Network/Disk I/O)
                    with tracer.start_as_current_span("git.execute_subprocess") as exec_span:
                        if not os.path.exists(repo_path):
                            exec_span.set_attribute("git.operation", "clone")
                            logger.info(f"📥 Cloning bare repo for {url}...")
                            subprocess.run(
                                ["git", "clone", "--mirror", "--filter=blob:none", url, repo_path],
                                check=True,
                                capture_output=True,
                            )
                        else:
                            exec_span.set_attribute("git.operation", "fetch")
                            logger.info(f"🔄 Fetching updates for {url}...")
                            subprocess.run(
                                ["git", "fetch", "--all", "--prune", "--filter=blob:none"],
                                cwd=repo_path,
                                check=True,
                                capture_output=True,
                            )

                except subprocess.CalledProcessError as e:
                    error_msg = e.stderr.decode() if e.stderr else str(e)
                    # [OTEL] Registriamo l'errore nello span
                    span.record_exception(e)
                    span.set_status(trace.Status(trace.StatusCode.ERROR))
                    logger.error(f"Git Operation Failed: {error_msg}")
                    raise e
                finally:
                    # 3. RILASCIO LOCK
                    fcntl.flock(f, fcntl.LOCK_UN)

            return repo_path

    def cleanup_orphaned_workspaces(self, max_age_seconds: int = 3600):
        """
        Garbage Collector (GC) for stalled or orphaned workspaces.

        Scans the `workspaces/` directory for folders older than `max_age_seconds` and removes them.
        This handles cases where a worker process crashes before it can clean up its ephemeral worktree.
        Also runs `git worktree prune` on cached repos to remove stale metadata.

        Args:
            max_age_seconds (int): Threshold for considering a workspace abandoned.
        """
        import time

        now = time.time()
        cutoff = now - max_age_seconds
        removed_count = 0

        # [OTEL] Tracciamo anche la GC per vedere se rallenta il sistema
        with tracer.start_as_current_span("git.gc.cleanup"):
            logger.info("🧹 [GC] Starting Cleanup of Orphaned Workspaces...")

            if os.path.exists(self.workspaces_dir):
                for item in os.listdir(self.workspaces_dir):
                    ws_path = os.path.join(self.workspaces_dir, item)
                    if not os.path.isdir(ws_path):
                        continue

                    try:
                        stat = os.stat(ws_path)
                        age = int(now - stat.st_mtime)

                        if stat.st_mtime < cutoff:
                            logger.warning(f"💀 [GC] Found Zombie Workspace '{item}' (Age: {age}s). Removing...")
                            shutil.rmtree(ws_path, ignore_errors=True)
                            removed_count += 1
                    except Exception as e:
                        logger.error(f"❌ [GC] Failed to remove {item}: {e}")

            if os.path.exists(self.cache_dir):
                for repo_name in os.listdir(self.cache_dir):
                    repo_path = os.path.join(self.cache_dir, repo_name)
                    if not os.path.isdir(repo_path) or not repo_name.endswith(".git"):
                        continue
                    try:
                        self._run_git(repo_path, ["worktree", "prune"])
                    except Exception as e:
                        logger.warning(f"⚠️ [GC] Failed to prune metadata for {repo_name}: {e}")

            # [OTEL] Log metrics as attributes
            trace.get_current_span().set_attribute("gc.removed_count", removed_count)

    def get_head_commit(self, url: str, branch: str = "main") -> str:
        repo_path = self._get_repo_cache_path(url)
        candidates = [branch, f"refs/heads/{branch}", f"refs/tags/{branch}"]

        # [OTEL] Span leggero per la risoluzione ref (CPU/Disk I/O veloce)
        with tracer.start_as_current_span("git.rev_parse"):
            for ref in candidates:
                cmd = ["git", "rev-parse", ref]
                try:
                    result = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True, check=True)
                    return result.stdout.strip()
                except subprocess.CalledProcessError:
                    continue

            raise ValueError(f"Ref '{branch}' not found in {url} (Checked: {candidates})")

    @contextlib.contextmanager
    def ephemeral_worktree(self, url: str, commit_hash: str) -> str:
        """
        Context Manager that provisions a temporary, isolated worktree for a specific commit.

        This mechanism allows concurrent analysis of different commits (or even the same commit)
        without the overhead of cloning the entire repo again. It uses `git worktree add`, which
        is lightweight and shares object storage with the bare repo cache.

        **Yields**:
            str: The absolute path to the temporary checkout directory.

        **Teardown**:
            Automatically removes the worktree directory and prunes git metadata upon exit.
        """
        repo_path = self._get_repo_cache_path(url)
        job_id = str(uuid.uuid4())
        workspace_path = os.path.join(self.workspaces_dir, job_id)

        # [OTEL] Span che copre l'intero ciclo di vita (Setup -> Use -> Teardown)
        with tracer.start_as_current_span("git.worktree.lifecycle") as span:
            span.set_attribute("worktree.id", job_id)
            span.set_attribute("commit.hash", commit_hash)

            try:
                # 1. SETUP (Copia fisica / Hardlink)
                with tracer.start_as_current_span("git.worktree.setup"):
                    logger.info(f"📂 Creating worktree for {commit_hash[:8]} at {workspace_path}")
                    subprocess.run(
                        ["git", "worktree", "add", "--detach", workspace_path, commit_hash],
                        cwd=repo_path,
                        check=True,
                        capture_output=True,
                    )

                yield workspace_path

            except subprocess.CalledProcessError as e:
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR))
                logger.error(f"Git worktree failed: {e.stderr.decode()}")
                raise e
            finally:
                # 2. TEARDOWN (Pulizia disco)
                # Spesso shutil.rmtree è lento su dischi grandi o con tanti file piccoli
                with tracer.start_as_current_span("git.worktree.teardown"):
                    if os.path.exists(workspace_path):
                        logger.info(f"🧹 Cleaning up workspace {job_id}")
                        subprocess.run(["git", "worktree", "prune"], cwd=repo_path, check=False, capture_output=True)
                        shutil.rmtree(workspace_path, ignore_errors=True)

    def _run_git(self, cwd, args):
        """Helper interno semplice"""
        subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True)
