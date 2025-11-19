import os
import sys
import json
import shutil
import subprocess
import tempfile
import sqlite3
import logging
from functools import lru_cache
from typing import List, Dict, Any, Set, Tuple, Optional, Generator
from collections import defaultdict

from ..base import BaseGraphIndexer, CodeRelation

logger = logging.getLogger(__name__)

# --- 1. SCIP CONSTANTS & UTILS ---
SCIP_ROLE_DEFINITION = 1
SCIP_ROLE_REFERENCE = 8
SCIP_ROLE_READ = 16
SCIP_ROLE_WRITE = 32
SCIP_ROLE_OVERRIDE = 64
SCIP_ROLE_IMPLEMENTATION = 128

def decode_scip_role(role_mask: int) -> str:
    roles = []
    if role_mask & SCIP_ROLE_DEFINITION: roles.append("definition")
    if role_mask & SCIP_ROLE_OVERRIDE: roles.append("override")
    if role_mask & SCIP_ROLE_IMPLEMENTATION: roles.append("implementation")
    if role_mask & SCIP_ROLE_WRITE: roles.append("write")
    if role_mask & SCIP_ROLE_READ: roles.append("read")
    if not roles or (role_mask & SCIP_ROLE_REFERENCE): roles.append("reference")
    return ",".join(roles)

def get_relation_verb(role_mask: int) -> str:
    if role_mask & SCIP_ROLE_DEFINITION: return "defines"
    if role_mask & SCIP_ROLE_OVERRIDE: return "overrides"
    if role_mask & SCIP_ROLE_IMPLEMENTATION: return "implements"
    if role_mask & SCIP_ROLE_WRITE: return "writes_to"
    if role_mask & SCIP_ROLE_READ: return "reads_from"
    return "calls"

# --- 2. DISK SYMBOL TABLE ---
class DiskSymbolTable:
    def __init__(self):
        self.db_path = tempfile.NamedTemporaryFile(delete=False, suffix=".db").name
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()
        self.cursor.execute("PRAGMA synchronous = OFF")
        self.cursor.execute("PRAGMA journal_mode = MEMORY")
        self.cursor.execute("PRAGMA cache_size = 10000")
        self.cursor.execute("""
            CREATE TABLE defs (
                symbol TEXT, scope_file TEXT, file_path TEXT,
                start_line INTEGER, start_char INTEGER, end_line INTEGER, end_char INTEGER,
                PRIMARY KEY (symbol, scope_file)
            )
        """)
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

# --- 3. SCIP RUNNER ---
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
    IGNORE_DIRS = {".git", "node_modules", "target", "build", "dist", ".venv", "venv", "__pycache__",".env", "env"}
    SCIP_CLI = "scip"

    def __init__(self, repo_path: str):
        self.repo_path = os.path.abspath(repo_path)

    def run_to_disk(self) -> Optional[str]:
        if not shutil.which(self.SCIP_CLI):
            logger.error(f"[SCIP] CLI '{self.SCIP_CLI}' non trovato.")
            return None
        
        installed = self._find_installed_indexers()
        if not installed: 
            logger.error("[SCIP] Nessun indexer installato.")
            return None
        
        tasks = self._discover_tasks()
        if not tasks: 
            logger.warning("[SCIP] Nessun progetto rilevato.")
            return None
        
        return self._execute_indexing_stream(tasks)

    def _find_installed_indexers(self) -> Set[str]:
        known = set(self.PROJECT_MARKERS.values()) | set(self.EXTENSION_MAP.values())
        return {idx for idx in known if shutil.which(idx)}

    def _discover_tasks(self) -> List[Tuple[str, str]]:
        tasks = []
        found_roots = set()
        for root, dirs, files in os.walk(self.repo_path, topdown=True):
            dirs[:] = [d for d in dirs if d.lower() not in self.IGNORE_DIRS]
            if any(root.startswith(p) for p in found_roots): continue
            for marker, indexer in self.PROJECT_MARKERS.items():
                if marker in files and shutil.which(indexer):
                    tasks.append((indexer, root))
                    found_roots.add(root)
                    dirs[:] = []
                    break
        if not tasks:
            detected = set()
            for root, _, files in os.walk(self.repo_path):
                for f in files:
                    ext = os.path.splitext(f)[1]
                    if ext in self.EXTENSION_MAP:
                        idx = self.EXTENSION_MAP[ext]
                        if shutil.which(idx): detected.add(idx)
            for idx in detected: tasks.append((idx, self.repo_path))
        return tasks

    def _execute_indexing_stream(self, tasks: List[Tuple[str, str]]) -> Optional[str]:
        output_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl").name
        env = os.environ.copy()
        env["PYTHONPATH"] = self.repo_path + os.pathsep + env.get("PYTHONPATH", "")

        try:
            with open(output_file, 'w', encoding='utf-8') as f_out:
                for indexer, project_root in tasks:
                    # Usiamo suffix .scip per obbligare il CLI a riconoscerlo
                    tmp_idx = tempfile.NamedTemporaryFile(delete=False, suffix=".scip").name
                    try:
                        logger.info(f"[SCIP] Esecuzione {indexer} in {project_root}")
                        
                        res = subprocess.run(
                            [indexer, "index", ".", "--output", tmp_idx],
                            cwd=project_root, check=False, capture_output=True, env=env
                        )
                        
                        # Controllo Errori Aggressivo
                        if res.returncode != 0:
                            logger.error(f"[SCIP FAIL] {indexer} failed (code {res.returncode}):")
                            logger.error(res.stderr.decode('utf-8', errors='replace'))
                        
                        # Check se il file esiste ed è valido
                        if not os.path.exists(tmp_idx) or os.path.getsize(tmp_idx) < 10:
                            logger.warning(f"[SCIP WARN] Indice vuoto o mancante per {project_root}")
                            continue

                        # Conversione a JSON
                        proc = subprocess.Popen(
                            [self.SCIP_CLI, "print", "--json", tmp_idx],
                            stdout=subprocess.PIPE, text=True
                        )
                        
                        count = 0
                        for line in proc.stdout:
                            if line.strip():
                                try:
                                    # Gestione sia JSON Lines che JSON Array
                                    payload = json.loads(line)
                                    docs = []
                                    if isinstance(payload, list): docs = payload # Array
                                    elif "documents" in payload: docs = payload["documents"] # Wrapper object
                                    else: docs = [payload] # Single doc (JSONL)
                                    
                                    for doc in docs:
                                        wrapper = {"project_root": project_root, "document": doc}
                                        f_out.write(json.dumps(wrapper) + "\n")
                                        count += 1
                                except json.JSONDecodeError: pass
                        
                        logger.info(f"[SCIP] Estratti {count} documenti da {project_root}")

                    except Exception as e:
                         logger.error(f"[SCIP] Errore durante processing {project_root}: {e}")
                    finally:
                        if os.path.exists(tmp_idx): os.remove(tmp_idx)
            return output_file
        except Exception as e:
            logger.error(f"[SCIP] Errore critico runner: {e}")
            if os.path.exists(output_file): os.remove(output_file)
            return None

# --- 4. INDEXER ---
class SCIPIndexer(BaseGraphIndexer):
    INDEXER_NAME = "scip"

    def __init__(self, repo_path: str):
        super().__init__(repo_path)
        self.repo_path = os.path.abspath(repo_path)

    def extract_relations(self, chunk_map: Dict) -> List[CodeRelation]:
        return list(self.stream_relations())

    def stream_relations(self, exclude_definitions=True, exclude_externals=False):
        runner = SCIPRunner(self.repo_path)
        json_path = runner.run_to_disk()
        if json_path:
            yield from self.stream_relations_from_file(json_path, exclude_definitions, exclude_externals)
            try: os.remove(json_path)
            except OSError: pass

    def stream_relations_from_file(self, json_path: str, exclude_definitions: bool = True, exclude_externals: bool = False) -> Generator[CodeRelation, None, None]:
        symbol_table = DiskSymbolTable()
        try:
            # FASE 1
            with open(json_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        w = json.loads(line); root, doc = w['project_root'], w['document']
                        if "relative_path" in doc and root:
                            abs_p = os.path.join(root, doc["relative_path"])
                            norm_p = sys.intern(os.path.relpath(abs_p, self.repo_path))
                            if not norm_p.startswith(".."):
                                for o in doc.get("occurrences", []):
                                    if o.get("symbol_roles", 0) & SCIP_ROLE_DEFINITION:
                                        is_local = o["symbol"].startswith("local")
                                        symbol_table.add(o["symbol"], norm_p, o["range"], is_local)
                    except ValueError: pass
            symbol_table.flush()

            # FASE 2
            with open(json_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        w = json.loads(line); root, doc = w['project_root'], w['document']
                        if "relative_path" in doc and root:
                            abs_p = os.path.join(root, doc["relative_path"])
                            norm_p = sys.intern(os.path.relpath(abs_p, self.repo_path))
                            if not norm_p.startswith(".."):
                                for o in doc.get("occurrences", []):
                                    roles = o.get("symbol_roles", 0)
                                    if exclude_definitions and (roles & SCIP_ROLE_DEFINITION): continue
                                    sym = o["symbol"]
                                    tgt_info = symbol_table.get(sym, norm_p)
                                    
                                    ext = False
                                    if tgt_info: tgt, tgt_rng = tgt_info
                                    elif not sym.startswith("local"):
                                        ext = True
                                        parts = sym.split()
                                        tgt = sys.intern(f"EXTERNAL::{parts[2]}::{parts[3]}") if len(parts)>=4 else "EXTERNAL::UNKNOWN"
                                        tgt_rng = []
                                    else: continue

                                    if exclude_externals and ext: continue

                                    verb = get_relation_verb(roles)
                                    s_bytes = self._bytes(norm_p, o["range"])
                                    t_bytes = None if ext else self._bytes(tgt, tgt_rng)

                                    yield CodeRelation(
                                        norm_p, tgt, verb,
                                        source_line=o["range"][0]+1, target_line=tgt_rng[0]+1 if not ext else 1,
                                        source_byte_range=s_bytes, target_byte_range=t_bytes,
                                        metadata={"tool": self.INDEXER_NAME, "description": self._make_desc(norm_p, tgt, verb, self._clean_symbol(sym), ext), "is_external": ext}
                                    )
                    except ValueError: pass
        finally: symbol_table.close()

    def _clean_symbol(self, raw: str) -> str:
        parts = raw.split()
        if not parts: return sys.intern(raw)
        cand = parts[-1].rstrip('.:`')
        if not cand or (cand[0].isdigit() and len(parts) > 1): cand = parts[-2].rstrip('.:`')
        return sys.intern(cand.replace('/', '.').replace('`', '')) if cand else "unknown"

    def _make_desc(self, src, tgt, verb, lbl, ext) -> str:
        src_n = os.path.basename(src)
        if ext:
            pkg = tgt.split("::")[-1] if "::" in tgt else tgt
            return f"Code in '{src_n}' {verb} external symbol '{lbl}' from '{pkg}'."
        return f"Code in '{src_n}' {verb} symbol '{lbl}' defined in '{os.path.basename(tgt)}'."

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
            sl, sc = rng[0], rng[1]
            el, ec = (sl, rng[2]) if len(rng)==3 else (rng[2], rng[3])
            if sl >= len(l) or el >= len(l): return None
            return [l[sl]+sc, l[el]+ec]
        except: return None