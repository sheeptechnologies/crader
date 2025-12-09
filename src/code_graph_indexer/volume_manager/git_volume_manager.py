import os
import shutil
import subprocess
import logging
import fcntl
import contextlib
import hashlib
import uuid
from typing import Optional

from ..config import STORAGE_ROOT

logger = logging.getLogger(__name__)

class GitVolumeManager:
    """
    Gestisce il ciclo di vita delle repository Git per l'indicizzazione.
    
    Pattern:
    1. Persistent Cache: ~/.codebase_store/cache/ (Bare Repos)
    2. Ephemeral Workspaces: ~/.codebase_store/workspaces/{uuid}/ (Worktrees)
    """
    def __init__(self):

        self.base_path = STORAGE_ROOT
        self.cache_dir = os.path.join(self.base_path, "cache")
        self.workspaces_dir = os.path.join(self.base_path, "workspaces")
        
        # Assicuriamoci che le directory esistano
        os.makedirs(self.cache_dir, exist_ok=True)
        os.makedirs(self.workspaces_dir, exist_ok=True)

    def _get_repo_cache_path(self, url: str) -> str:
        # Usiamo un hash dell'URL per creare un nome directory sicuro e univoco
        safe_name = hashlib.sha256(url.encode()).hexdigest()
        return os.path.join(self.cache_dir, f"{safe_name}.git")

    def ensure_repo_updated(self, url: str) -> str:
        """
        Garantisce che la Bare Repo locale esista e sia aggiornata.
        [FIX] Il Lock ora protegge SIA il Clone CHE il Fetch per evitare race conditions.
        """
        repo_path = self._get_repo_cache_path(url)
        lock_file = f"{repo_path}.lock"
        
        # Apriamo (o creiamo) il file di lock.
        # Nota: 'w' tronca il file, ma per un lock file va bene (ci interessa solo il file descriptor).
        with open(lock_file, 'w') as f:
            try:
                # 1. ACQUISIZIONE LOCK (Bloccante)
                # Chi arriva dopo aspetta qui finché il primo non finisce.
                fcntl.flock(f, fcntl.LOCK_EX) 
                
                # 2. CHECK & EXECUTE (Safe Zone)
                if not os.path.exists(repo_path):
                    # Scenario A: Repo non esiste -> CLONE
                    logger.info(f"📥 Cloning bare repo for {url}...")
                    subprocess.run(
                        ["git", "clone", "--mirror","--filter=blob:none", url, repo_path],
                        check=True, 
                        capture_output=True
                    )
                else:
                    # Scenario B: Repo esiste -> FETCH
                    logger.info(f"🔄 Fetching updates for {url}...")
                    subprocess.run(
                        ["git", "fetch", "--all", "--prune", "--filter=blob:none"], 
                        cwd=repo_path, 
                        check=True,
                        capture_output=True
                    )
                    
            except subprocess.CalledProcessError as e:
                error_msg = e.stderr.decode() if e.stderr else str(e)
                logger.error(f"Git Operation Failed: {error_msg}")
                raise e
            finally:
                # 3. RILASCIO LOCK
                fcntl.flock(f, fcntl.LOCK_UN)
        
        return repo_path

    def cleanup_orphaned_workspaces(self, max_age_seconds: int = 3600):
        """
        Esegue una Garbage Collection profonda (Filesystem + Git Metadata).
        Rimuove i workspace residui da crash precedenti e allinea lo stato di Git.
        """
        import time
        now = time.time()
        cutoff = now - max_age_seconds
        removed_count = 0
        
        logger.info("🧹 [GC] Starting Cleanup of Orphaned Workspaces...")

        # 1. FASE FILESYSTEM: Rimozione cartelle fisiche obsolete
        if os.path.exists(self.workspaces_dir):
            for item in os.listdir(self.workspaces_dir):
                ws_path = os.path.join(self.workspaces_dir, item)
                
                # Ignora file sciolti, ci interessano solo le directory dei job
                if not os.path.isdir(ws_path): continue
                
                try:
                    # Controlliamo l'ultima modifica. Se è vecchio, è uno zombie.
                    stat = os.stat(ws_path)
                    age = int(now - stat.st_mtime)
                    
                    if stat.st_mtime < cutoff:
                        logger.warning(f"💀 [GC] Found Zombie Workspace '{item}' (Age: {age}s). Removing...")
                        
                        # Rimozione fisica ricorsiva (Force)
                        shutil.rmtree(ws_path, ignore_errors=True)
                        removed_count += 1
                except Exception as e:
                    logger.error(f"❌ [GC] Failed to remove {item}: {e}")

        # 2. FASE GIT METADATA: Pruning dei riferimenti nelle repo cache
        # Git tiene traccia dei worktree in .git/worktrees. Se cancelliamo la cartella, 
        # git si lamenta. 'git worktree prune' risolve l'incongruenza.
        if os.path.exists(self.cache_dir):
            for repo_name in os.listdir(self.cache_dir):
                repo_path = self.cache_dir / repo_name
                
                # Processiamo solo le bare repo (.git)
                if not repo_path.is_dir() or not repo_name.endswith(".git"): continue
                
                try:
                    # 'prune' rimuove le informazioni sui worktree che non esistono più su disco
                    # È veloce e sicuro.
                    self._run_git(repo_path, ["worktree", "prune"])
                except Exception as e:
                    # Non blocchiamo il processo per un errore di prune, logghiamo e via
                    logger.warning(f"⚠️ [GC] Failed to prune metadata for {repo_name}: {e}")
        
        if removed_count > 0:
            logger.info(f"✨ [GC] Completed. Removed {removed_count} zombie workspaces.")
        else:
            logger.info("✨ [GC] Clean. No zombies found.")

    def get_head_commit(self, url: str, branch: str = "main") -> str:
        """
        Risolve l'hash SHA del commit HEAD per un dato branch.
        """
        repo_path = self._get_repo_cache_path(url)
        
        # [FIX] In una Bare/Mirror repo, i branch sono refs locali o tags.
        # Non esiste 'origin/main'. Cerchiamo il ref esatto.
        
        # Tentativo 1: Branch locale (es. 'main')
        candidates = [branch, f"refs/heads/{branch}", f"refs/tags/{branch}"]
        
        for ref in candidates:
            cmd = ["git", "rev-parse", ref]
            try:
                result = subprocess.run(
                    cmd, cwd=repo_path, capture_output=True, text=True, check=True
                )
                return result.stdout.strip()
            except subprocess.CalledProcessError:
                continue
                
        # Se fallisce tutto, lanciamo errore
        raise ValueError(f"Ref '{branch}' not found in {url} (Checked: {candidates})")

    @contextlib.contextmanager
    def ephemeral_worktree(self, url: str, commit_hash: str) -> str:
        """
        Crea un worktree temporaneo isolato per uno specifico commit.
        Garantisce la pulizia (rimozione cartella) alla fine del blocco 'with'.
        """
        repo_path = self._get_repo_cache_path(url)
        
        # ID univoco per questo job di indicizzazione
        job_id = str(uuid.uuid4())
        workspace_path = os.path.join(self.workspaces_dir, job_id)
        
        try:
            logger.info(f"📂 Creating worktree for {commit_hash[:8]} at {workspace_path}")
            # Creiamo il worktree 'detached' su quel commit
            subprocess.run(
                ["git", "worktree", "add", "--detach", workspace_path, commit_hash],
                cwd=repo_path,
                check=True,
                capture_output=True
            )
            
            yield workspace_path
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Git worktree failed: {e.stderr.decode()}")
            raise e
        finally:
            # CLEANUP: Rimuoviamo il worktree
            if os.path.exists(workspace_path):
                logger.info(f"🧹 Cleaning up workspace {job_id}")
                # 1. Prune amministrativo lato git (ignora errori se già rimosso)
                subprocess.run(
                    ["git", "worktree", "prune"], 
                    cwd=repo_path, 
                    check=False, capture_output=True
                )
                # 2. Rimozione fisica file system
                shutil.rmtree(workspace_path, ignore_errors=True)