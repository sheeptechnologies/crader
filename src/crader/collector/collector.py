import os
import subprocess
import logging
import stat
from typing import List, Generator, Optional, Tuple

# [OTEL] Trace API
from opentelemetry import trace

from .schema import CollectedFile, FileCategory
from .config import SUPPORTED_EXTENSIONS, BLOCKLIST_DIRS, MAX_FILE_SIZE_BYTES

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

class SourceCollector:
    """
    Handles discovery, validation, and enrichment of source files.
    
    It leverages 'git ls-files' for high-performance scanning and applies 
    strict safety filters (blocklists, size limits, binary checks) before 
    passing files to the indexing pipeline.
    """
    
    def __init__(self, repo_root: str):
        self.repo_root = os.path.abspath(repo_root)
        self.valid_exts = SUPPORTED_EXTENSIONS
        self.blocklist = BLOCKLIST_DIRS
        self.max_size = MAX_FILE_SIZE_BYTES

    def stream_files(self, chunk_size: int = 2000) -> Generator[List[CollectedFile], None, None]:
        """
        Main Generator. Yields batches of valid, enriched CollectedFile objects.
        
        Args:
            chunk_size: Number of files per batch (default: 2000).
        """
        with tracer.start_as_current_span("collector.stream_files") as span:
            span.set_attribute("repo.root", self.repo_root)
            
            count = 0
            buffer = []

            def flush():
                nonlocal buffer
                if buffer:
                    yield buffer
                    buffer = []

            # --- PHASE 1: Tracked Files (With Git Hash) ---
            # git ls-files -s (stage) -z (null terminator)
            # This provides the SHA-1 blob hash "for free".
            cmd_tracked = [
                "git", "-C", self.repo_root,
                "ls-files", "-s", "-z", "--exclude-standard"
            ]
            
            for rel_path, git_hash in self._run_git_stream(cmd_tracked, parse_staged=True):
                file_obj = self._validate_and_build(rel_path, git_hash)
                if file_obj:
                    buffer.append(file_obj)
                    count += 1
                    if len(buffer) >= chunk_size:
                        yield buffer
                        buffer = []

            # --- PHASE 2: Untracked Files (Without Hash) ---
            # git ls-files -o (others) -z
            # Handles new files that are not yet committed but present in the workspace.
            cmd_untracked = [
                "git", "-C", self.repo_root,
                "ls-files", "-o", "-z", "--exclude-standard"
            ]
            
            for rel_path, _ in self._run_git_stream(cmd_untracked, parse_staged=False):
                file_obj = self._validate_and_build(rel_path, git_hash=None)
                if file_obj:
                    buffer.append(file_obj)
                    count += 1
                    if len(buffer) >= chunk_size:
                        yield buffer
                        buffer = []

            # Final Flush
            yield from flush()
            
            span.set_attribute("collector.total_files", count)
            logger.info(f"✨ Collection complete. Found {count} valid files.")

    def _run_git_stream(self, cmd: List[str], parse_staged: bool) -> Generator[Tuple[str, Optional[str]], None, None]:
        """
        Executes the Git command and parses the raw binary stream.
        Uses subprocess.Popen to avoid loading huge outputs into RAM.
        """
        try:
            with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
                # For repos < 1M files, reading stdout into memory is safe and efficient.
                # 'communicate' handles potential deadlocks better than manual reads.
                stdout, stderr = proc.communicate()
                
                if proc.returncode != 0:
                    err_msg = stderr.decode(errors='replace')
                    logger.warning(f"Git command failed: {err_msg}")
                    trace.get_current_span().record_exception(Exception(err_msg))
                    return

                # Fast split on null bytes (CPython optimized)
                entries = stdout.split(b'\0')
                
                for entry in entries:
                    if not entry: continue
                    try:
                        if parse_staged:
                            # Format: "100644 <hash> 0\t<path>"
                            # We limit the split to ensure path safety
                            meta, path_bytes = entry.split(b'\t', 1)
                            meta_parts = meta.split(b' ')
                            if len(meta_parts) >= 2:
                                git_hash = meta_parts[1].decode('ascii')
                                rel_path = path_bytes.decode('utf-8', errors='replace')
                                yield rel_path, git_hash
                        else:
                            # Format: "<path>"
                            rel_path = entry.decode('utf-8', errors='replace')
                            yield rel_path, None
                    except ValueError:
                        continue

        except Exception as e:
            logger.error(f"Error in git stream: {e}")
            trace.get_current_span().record_exception(e)

    def _validate_and_build(self, rel_path: str, git_hash: Optional[str]) -> Optional[CollectedFile]:
        """
        Applies the collection funnel: Metadata Filter -> Filesystem Check -> Enrichment.
        """
        # 1. Extension Filter (In-Memory, Fast)
        _, ext = os.path.splitext(rel_path)
        ext = ext.lower()
        if ext not in self.valid_exts:
            return None

        # 2. Blocklist Filter (In-Memory, Fast)
        # Optimization: fast string check first, then precise path check
        if any(b in rel_path for b in self.blocklist):
            parts = rel_path.split(os.sep)
            if any(p in self.blocklist for p in parts):
                return None

        full_path = os.path.join(self.repo_root, rel_path)

        # 3. Filesystem Safety Check (I/O, Slow)
        try:
            # lstat does NOT follow symlinks (crucial for loop prevention)
            st = os.lstat(full_path)
            
            # Reject non-regular files (Symlinks, Directories, Sockets, Devices)
            if not stat.S_ISREG(st.st_mode):
                return None
            
            # Size Limit Check
            if st.st_size == 0 or st.st_size > self.max_size:
                return None

            # 4. Semantic Enrichment (CPU)
            category = self._determine_category(rel_path)

            return CollectedFile(
                rel_path=rel_path,
                full_path=full_path,
                extension=ext,
                size_bytes=st.st_size,
                git_hash=git_hash,
                category=category
            )

        except OSError:
            # File might have been deleted between git listing and lstat
            return None

    def _determine_category(self, rel_path: str) -> FileCategory:
        """
        Heuristically determines the semantic category of a file based on its path.
        """
        path_lower = rel_path.lower()
        parts = path_lower.split(os.sep)
        name = os.path.basename(path_lower)

        # A. TEST FILES
        if any(x in parts for x in ['test', 'tests', 'testing', 'spec', 'specs', '__tests__']):
            return 'test'
        if name.startswith('test_') or name.endswith('_test.py') or name.endswith('_test.go') or name.endswith('.test.js') or name.endswith('.spec.ts'):
            return 'test'

        # B. CONFIGURATION
        if name in {'package.json', 'requirements.txt', 'pyproject.toml', 'dockerfile', 'makefile', 'go.mod', 'cargo.toml'}:
            return 'config'
        if name.endswith('.yml') or name.endswith('.yaml') or name.endswith('.json') or name.endswith('.toml'):
            return 'config'
        
        # C. DOCUMENTATION
        if any(x in parts for x in ['doc', 'docs', 'documentation', 'guides']):
            return 'docs'
        if name.endswith('.md') or name.endswith('.rst') or name.endswith('.txt'):
            return 'docs'

        # D. SOURCE CODE (Default)
        return 'source'