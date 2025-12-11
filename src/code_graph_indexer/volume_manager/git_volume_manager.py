import os
import shutil
import subprocess
import logging
import fcntl
import contextlib
import hashlib
import uuid
from typing import Optional

# [OTEL] Import Trace API
from opentelemetry import trace

from ..config import STORAGE_ROOT

logger = logging.getLogger(__name__)

# [OTEL] Inizializzazione Tracer
tracer = trace.get_tracer(__name__)

class GitVolumeManager:
    """
    Gestisce il ciclo di vita delle repository Git per l'indicizzazione.
    Strumentato con OpenTelemetry per tracciare I/O e Lock Contention.
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
        Garantisce che la Bare Repo locale esista e sia aggiornata.
        """
        repo_path = self._get_repo_cache_path(url)
        lock_file = f"{repo_path}.lock"
        
        # [OTEL] Span principale per l'operazione di sync
        with tracer.start_as_current_span("git.ensure_updated") as span:
            span.set_attribute("repo.url", url)
            span.set_attribute("repo.path", repo_path)

            with open(lock_file, 'w') as f:
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
                                ["git", "clone", "--mirror","--filter=blob:none", url, repo_path],
                                check=True, 
                                capture_output=True
                            )
                        else:
                            exec_span.set_attribute("git.operation", "fetch")
                            logger.info(f"🔄 Fetching updates for {url}...")
                            subprocess.run(
                                ["git", "fetch", "--all", "--prune", "--filter=blob:none"], 
                                cwd=repo_path, 
                                check=True,
                                capture_output=True
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
        GC dei workspace orfani.
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
                    if not os.path.isdir(ws_path): continue
                    
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
                    if not os.path.isdir(repo_path) or not repo_name.endswith(".git"): continue
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
                    result = subprocess.run(
                        cmd, cwd=repo_path, capture_output=True, text=True, check=True
                    )
                    return result.stdout.strip()
                except subprocess.CalledProcessError:
                    continue
                    
            raise ValueError(f"Ref '{branch}' not found in {url} (Checked: {candidates})")

    @contextlib.contextmanager
    def ephemeral_worktree(self, url: str, commit_hash: str) -> str:
        """
        Crea un worktree temporaneo isolato.
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
                        capture_output=True
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
                        subprocess.run(
                            ["git", "worktree", "prune"], 
                            cwd=repo_path, 
                            check=False, capture_output=True
                        )
                        shutil.rmtree(workspace_path, ignore_errors=True)

    def _run_git(self, cwd, args):
        """Helper interno semplice"""
        subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True)