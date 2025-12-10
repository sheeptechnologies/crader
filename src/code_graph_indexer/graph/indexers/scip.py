import os
import sys
import json
import shutil
import subprocess
import tempfile
import sqlite3
import logging
import concurrent.futures
from functools import lru_cache
from typing import List, Dict, Any, Set, Tuple, Optional, Generator

from ..base import BaseGraphIndexer, CodeRelation

from ...parsing.parsing_filters import GLOBAL_IGNORE_DIRS, SEMANTIC_NOISE_DIRS

logger = logging.getLogger(__name__)

# --- SCIP CONSTANTS & UTILS ---
SCIP_ROLE_DEFINITION = 1
SCIP_ROLE_REFERENCE = 8
SCIP_ROLE_READ = 16
SCIP_ROLE_WRITE = 32
SCIP_ROLE_OVERRIDE = 64
SCIP_ROLE_IMPLEMENTATION = 128

def get_relation_verb(role_mask: int) -> str:
    if role_mask & SCIP_ROLE_DEFINITION: return "defines"
    if role_mask & SCIP_ROLE_OVERRIDE: return "overrides"
    if role_mask & SCIP_ROLE_IMPLEMENTATION: return "implements"
    if role_mask & SCIP_ROLE_WRITE: return "writes_to"
    if role_mask & SCIP_ROLE_READ: return "reads_from"
    return "calls"

# --- 2. DISK SYMBOL TABLE (Invariata) ---
class DiskSymbolTable:
    def __init__(self):
        self.db_path = tempfile.NamedTemporaryFile(delete=False, suffix=".db").name
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()
        self.cursor.execute("PRAGMA synchronous = OFF")
        self.cursor.execute("PRAGMA journal_mode = MEMORY")
        self.cursor.execute("CREATE TABLE defs (symbol TEXT, scope_file TEXT, file_path TEXT, start_line INTEGER, start_char INTEGER, end_line INTEGER, end_char INTEGER, PRIMARY KEY (symbol, scope_file))")
        self.buffer = []

    def add(self, symbol: str, file_path: str, scip_range: List[int], is_local: bool):
        scope = file_path if is_local else ""
        s_line, s_char = scip_range[0], scip_range[1]
        el = scip_range[2] if len(scip_range) > 3 else s_line
        ec = scip_range[3] if len(scip_range) > 3 else scip_range[2]
        self.buffer.append((symbol, scope, file_path, s_line, s_char, el, ec))
        if len(self.buffer) >= 10000: self.flush()

    def flush(self):
        if self.buffer:
            self.cursor.executemany("INSERT OR REPLACE INTO defs VALUES (?, ?, ?, ?, ?, ?, ?)", self.buffer)
            self.conn.commit()
            self.buffer = []

    def get(self, symbol: str, current_file: str) -> Optional[Tuple[str, List[int]]]:
        self.cursor.execute("SELECT file_path, start_line, start_char, end_line, end_char FROM defs WHERE symbol = ? AND scope_file = ?", (symbol, current_file))
        row = self.cursor.fetchone()
        if not row:
            self.cursor.execute("SELECT file_path, start_line, start_char, end_line, end_char FROM defs WHERE symbol = ? AND scope_file = ''", (symbol,))
            row = self.cursor.fetchone()
        if row: return row[0], [row[1], row[2], row[3], row[4]]
        return None

    def close(self):
        self.flush()
        self.conn.close()
        if os.path.exists(self.db_path):
            try: os.remove(self.db_path)
            except OSError: pass

# --- 3. SCIP RUNNER (STREAMING) ---
class SCIPRunner:
    PROJECT_MARKERS = {
        "pyproject.toml": "scip-python", "requirements.txt": "scip-python", "setup.py": "scip-python",
        "package.json": "scip-typescript", "tsconfig.json": "scip-typescript",
        "pom.xml": "scip-java", "build.gradle": "scip-java",
        "go.mod": "scip-go", "Cargo.toml": "scip-rust",
        "composer.json": "scip-php", "compile_commands.json": "scip-clang",
    }
    EXTENSION_MAP = {
        ".py": "scip-python", ".ts": "scip-typescript", ".js": "scip-typescript",
        ".java": "scip-java", ".go": "scip-go", ".rs": "scip-rust",
        ".php": "scip-php", ".c": "scip-clang", ".cpp": "scip-clang",
    }

    SCIP_CLI = "scip"

    def __init__(self, repo_path: str):
        self.repo_path = os.path.abspath(repo_path)
        self._active_indices = []
        # SCIP deve essere più aggressivo del parser nell'ignorare cose.
        self.ignore_dirs = GLOBAL_IGNORE_DIRS | SEMANTIC_NOISE_DIRS

    def prepare_indices(self) -> List[Tuple[str, str]]:
        if not shutil.which(self.SCIP_CLI):
            logger.error(f"[SCIP] CLI '{self.SCIP_CLI}' non trovato.")
            return []
        
        tasks = self._discover_tasks()
        if not tasks: return []
        
        results = []
        env = os.environ.copy()
        env["PYTHONPATH"] = self.repo_path + os.pathsep + env.get("PYTHONPATH", "")

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(tasks), 4)) as executor:
            future_to_task = {
                executor.submit(self._run_single_index, t, env): t for t in tasks
            }
            for future in concurrent.futures.as_completed(future_to_task):
                res = future.result()
                if res:
                    results.append(res)
                    self._active_indices.append(res[1])
        return results

    def _run_single_index(self, task, env) -> Optional[Tuple[str, str]]:

        indexer, project_root = task
        tmp_idx = tempfile.NamedTemporaryFile(delete=False, suffix=".scip").name
        try:
            logger.info(f"[SCIP] Indexing {project_root} with {indexer}...")
            subprocess.run(
                [indexer, "index", ".", "--output", tmp_idx],
                cwd=project_root, check=False, capture_output=True, env=env
            )
            if os.path.exists(tmp_idx) and os.path.getsize(tmp_idx) > 10:
                return (project_root, tmp_idx)
        except Exception as e:
            logger.error(f"[SCIP] Error {indexer}: {e}")
        return None
    

    def stream_documents(self, indices: List[Tuple[str, str]]) -> Generator[Dict, None, None]:
        for project_root, index_path in indices:
            try:
                proc = subprocess.Popen(
                    [self.SCIP_CLI, "print", "--json", index_path],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
                )
                for line in proc.stdout:
                    if not line.strip(): continue
                    try:
                        payload = json.loads(line)
                        docs = payload if isinstance(payload, list) else payload.get("documents", [payload])
                        for doc in docs:
                            # [NEW] Filtraggio Post-Processing per i documenti
                            # Se l'indexer ha incluso file che volevamo ignorare (es. tests), li scartiamo qui.
                            if self._should_skip_document(doc.get("relative_path", "")):
                                continue
                            yield {"project_root": project_root, "document": doc}
                    except ValueError: pass
                proc.wait()
            except Exception as e:
                logger.error(f"[SCIP] Stream error for {project_root}: {e}")

    def _should_skip_document(self, rel_path: str) -> bool:
        """Controlla se il file restituito da SCIP è in una directory ignorata."""
        if not rel_path: return True
        parts = rel_path.split('/')
        # Se una qualsiasi parte del path è nella blacklist, scarta
        for part in parts:
            if part in self.ignore_dirs or part.startswith('.'):
                return True
        return False

    def cleanup(self):
        for p in self._active_indices:
            try: os.remove(p)
            except: pass
        self._active_indices = []

    def _discover_tasks(self) -> List[Tuple[str, str]]:
        tasks = []
        found_roots = set()
        
        # Usa self.ignore_dirs (che include SEMANTIC_NOISE) per potare l'albero
        for root, dirs, files in os.walk(self.repo_path, topdown=True):
            # Modifica in-place di dirs per non scendere in cartelle ignorate
            dirs[:] = [d for d in dirs if d not in self.ignore_dirs and not d.startswith('.')]
            
            if any(root.startswith(p) for p in found_roots): continue
            
            for marker, indexer in self.PROJECT_MARKERS.items():
                if marker in files and shutil.which(indexer):
                    tasks.append((indexer, root))
                    found_roots.add(root)
                    dirs[:] = [] # Stop recursion in this project
                    break
        
        if not tasks:
            detected = set()
            for root, _, files in os.walk(self.repo_path):
                # Anche qui filtriamo implicitamente perché os.walk sopra ha già potato 'dirs'
                for f in files:
                    ext = os.path.splitext(f)[1]
                    if ext in self.EXTENSION_MAP:
                        idx = self.EXTENSION_MAP[ext]
                        if shutil.which(idx): detected.add(idx)
            for idx in detected: tasks.append((idx, self.repo_path))
        return tasks

    def _find_installed_indexers(self): return {} 

# --- 4. INDEXER (PIPELINED & FILTERED) ---
class SCIPIndexer(BaseGraphIndexer):
    INDEXER_NAME = "scip"

    def __init__(self, repo_path: str):
        super().__init__(repo_path)
        self.repo_path = os.path.abspath(repo_path)
        self.runner = SCIPRunner(repo_path)

    def extract_relations(self, chunk_map: Dict) -> List[CodeRelation]:
        return list(self.stream_relations())

    def stream_relations(self, exclude_definitions: bool = True, exclude_externals: bool = True) -> Generator[CodeRelation, None, None]:
        """
        Generatore principale con filtraggio.
        """
        indices = self.runner.prepare_indices()
        if not indices: return

        symbol_table = DiskSymbolTable()
        try:
            # PASS 1: Popola Symbol Table
            for wrapper in self.runner.stream_documents(indices):
                self._process_definitions(wrapper, symbol_table)
            
            symbol_table.flush()

            # PASS 2: Genera Relazioni con Filtri
            for wrapper in self.runner.stream_documents(indices):
                yield from self._process_occurrences(wrapper, symbol_table, exclude_definitions, exclude_externals)
                
        finally:
            symbol_table.close()
            self.runner.cleanup()

    def _process_definitions(self, wrapper: Dict, table: DiskSymbolTable):
        root, doc = wrapper['project_root'], wrapper['document']
        if "relative_path" in doc and root:
            abs_p = os.path.join(root, doc["relative_path"])
            norm_p = sys.intern(os.path.relpath(abs_p, self.repo_path))
            if not norm_p.startswith(".."):
                for o in doc.get("occurrences", []):
                    if o.get("symbol_roles", 0) & SCIP_ROLE_DEFINITION:
                        is_local = o["symbol"].startswith("local")
                        table.add(o["symbol"], norm_p, o["range"], is_local)

    def _process_occurrences(self, wrapper: Dict, table: DiskSymbolTable, exclude_definitions: bool, exclude_externals: bool) -> Generator[CodeRelation, None, None]:
        root, doc = wrapper['project_root'], wrapper['document']
        if "relative_path" not in doc or not root: return
        
        abs_p = os.path.join(root, doc["relative_path"])
        norm_p = sys.intern(os.path.relpath(abs_p, self.repo_path))
        if norm_p.startswith(".."): return

        for o in doc.get("occurrences", []):
            roles = o.get("symbol_roles", 0)
            
            # [FILTER] Exclude Definitions (Auto-reference nodes)
            if exclude_definitions and (roles & SCIP_ROLE_DEFINITION): 
                continue 
            
            raw_sym = o["symbol"]
            is_local = raw_sym.startswith("local")
            tgt_info = table.get(raw_sym, norm_p)
            
            ext = False
            if tgt_info: 
                tgt, tgt_rng = tgt_info
            elif not is_local:
                ext = True
                parts = raw_sym.split()
                # Creiamo un ID fittizio per l'esterno, ma non lo useremo se filtriamo
                tgt = sys.intern(f"EXTERNAL::{parts[2]}::{parts[3]}") if len(parts)>=4 else "EXTERNAL::UNKNOWN"
                tgt_rng = []
            else: continue 

            # [FILTER] Exclude Externals (Prevention of FK Error)
            if exclude_externals and ext:
                continue

            verb = get_relation_verb(roles)
            clean_sym = self._extract_symbol_name(norm_p, o["range"]) if is_local else self._clean_symbol(raw_sym)
            if not clean_sym or clean_sym == "unknown": continue

            yield CodeRelation(
                norm_p, tgt, verb,
                source_line=o["range"][0]+1, target_line=tgt_rng[0]+1 if not ext else 1,
                source_byte_range=self._bytes(norm_p, o["range"]),
                target_byte_range=None if ext else self._bytes(tgt, tgt_rng),
                metadata={
                    "tool": self.INDEXER_NAME,
                    "symbol": clean_sym,
                    "is_external": ext
                }
            )

    # --- Helpers ---
    @lru_cache(maxsize=1024)
    def _get_file_content_cached(self, rel_path: str) -> Optional[List[str]]:
        abs_path = os.path.join(self.repo_path, rel_path)
        if not os.path.exists(abs_path): return None
        try:
            if os.path.getsize(abs_path) > 1024 * 1024: return None 
            with open(abs_path, 'r', encoding='utf-8', errors='ignore') as f: return f.readlines()
        except: return None

    def _extract_symbol_name(self, rel_path: str, rng: List[int]) -> str:
        lines = self._get_file_content_cached(rel_path)
        if not lines: return "unknown"
        try:
            sl, sc = rng[0], rng[1]
            el, ec = (sl, rng[2]) if len(rng) == 3 else (rng[2], rng[3])
            if sl >= len(lines): return "unknown"
            return lines[sl][sc:ec] if sl == el else "unknown"
        except: return "unknown"

    def _clean_symbol(self, raw: str) -> str:
        parts = raw.split()
        if not parts: return sys.intern(raw)
        desc = parts[-1]
        for ext in ['.py/', '.ts/', '.js/', '.java/', '.go/']:
            if ext in desc: desc = desc.split(ext)[-1]; break
        return sys.intern(desc.replace('/', '.').replace('#', '.').rstrip('.'))

    @lru_cache(maxsize=64)
    def _lines(self, p):
        ap = os.path.join(self.repo_path, p)
        if not os.path.exists(ap): return None
        try:
            with open(ap, 'rb') as f: return [0] + [i+1 for i, b in enumerate(f.read()) if b==10]
        except: return None

    def _bytes(self, p, rng):
        l = self._lines(p)
        if not l: return None
        try:
            sl = rng[0]; el = rng[2] if len(rng)>3 else rng[0]
            if sl >= len(l): return None
            return [l[sl]+rng[1], l[el]+(rng[3] if len(rng)>3 else rng[2])]
        except: return None