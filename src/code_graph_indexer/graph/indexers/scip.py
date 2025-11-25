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
                        
                        if res.returncode != 0:
                            logger.error(f"[SCIP FAIL] {indexer} failed (code {res.returncode}):")
                            logger.error(res.stderr.decode('utf-8', errors='replace'))
                        
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
                                    payload = json.loads(line)
                                    docs = []
                                    if isinstance(payload, list): docs = payload
                                    elif "documents" in payload: docs = payload["documents"]
                                    else: docs = [payload]
                                    
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
            # FASE 1: Popolamento definizioni (Invariata)
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

            # FASE 2: Generazione Relazioni (Arricchita)
            with open(json_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        w = json.loads(line); root, doc = w['project_root'], w['document']
                        if "relative_path" in doc and root:
                            abs_p = os.path.join(root, doc["relative_path"])
                            norm_p = sys.intern(os.path.relpath(abs_p, self.repo_path))
                            
                            if norm_p.startswith(".."): continue

                            for o in doc.get("occurrences", []):
                                roles = o.get("symbol_roles", 0)
                                if exclude_definitions and (roles & SCIP_ROLE_DEFINITION): continue
                                
                                raw_sym = o["symbol"]
                                is_local_id = raw_sym.startswith("local")
                                
                                tgt_info = symbol_table.get(raw_sym, norm_p)
                                
                                ext = False
                                if tgt_info: 
                                    tgt, tgt_rng = tgt_info
                                elif not is_local_id:
                                    ext = True
                                    parts = raw_sym.split()
                                    tgt = sys.intern(f"EXTERNAL::{parts[2]}::{parts[3]}") if len(parts)>=4 else "EXTERNAL::UNKNOWN"
                                    tgt_rng = []
                                else: 
                                    continue 

                                if exclude_externals and ext: continue

                                verb = get_relation_verb(roles)
                                s_bytes = self._bytes(norm_p, o["range"])
                                t_bytes = None if ext else self._bytes(tgt, tgt_rng)
                                
                                # --- FIX NOME SIMBOLO ---
                                if is_local_id:
                                    # Simbolo locale: leggiamo il nome vero dal codice
                                    real_name = self._extract_symbol_name(norm_p, o["range"])
                                    clean_sym = sys.intern(real_name)
                                    sym_type = "variable" 
                                else:
                                    # Simbolo globale: pulizia intelligente
                                    clean_sym = self._clean_symbol(raw_sym)
                                    sym_type = self._infer_symbol_type(raw_sym)

                                if not clean_sym or clean_sym == "unknown": continue

                                yield CodeRelation(
                                    norm_p, tgt, verb,
                                    source_line=o["range"][0]+1, target_line=tgt_rng[0]+1 if not ext else 1,
                                    source_byte_range=s_bytes, target_byte_range=t_bytes,
                                    metadata={
                                        "tool": self.INDEXER_NAME,
                                        "description": self._make_desc(norm_p, tgt, verb, clean_sym, ext),
                                        "is_external": ext,
                                        "symbol": clean_sym,
                                        "symbol_type": sym_type,
                                        "edge_category": "semantic"
                                    }
                                )
                    except ValueError: pass
        finally: symbol_table.close()

    @lru_cache(maxsize=128)
    def _get_file_content_cached(self, rel_path: str) -> Optional[List[str]]:
        """Cache per lettura file sorgenti (necessaria per simboli locali)."""
        abs_path = os.path.join(self.repo_path, rel_path)
        if not os.path.exists(abs_path): return None
        try:
            # Skip file enormi per performance
            if os.path.getsize(abs_path) > 1024 * 1024: return None 
            with open(abs_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.readlines()
        except: return None

    def _extract_symbol_name(self, rel_path: str, rng: List[int]) -> str:
        """Estrae il nome variabile dal codice sorgente dato il range."""
        lines = self._get_file_content_cached(rel_path)
        if not lines: return "unknown"
        try:
            sl, sc = rng[0], rng[1]
            el, ec = (sl, rng[2]) if len(rng) == 3 else (rng[2], rng[3])
            
            if sl >= len(lines): return "unknown"
            if sl == el:
                line = lines[sl]
                if sc >= len(line) or ec > len(line): return "unknown"
                return line[sc:ec]
            return "unknown" 
        except: return "unknown"

    def _clean_symbol(self, raw: str) -> str:
        """
        Pulisce il simbolo rimuovendo path file e path modulo (package).
        Input:  '.../server.py/debug_tool.server.DbAdapter#NodeView.'
        Output: 'DbAdapter.NodeView'
        """
        parts = raw.split()
        if not parts: return sys.intern(raw)
        
        descriptor = parts[-1]
        
        # 1. Strip File Extension Prefix (es. .../server.py/)
        common_extensions = ['.py', '.ts', '.js', '.jsx', '.tsx', '.java', '.go', '.rs', '.php', '.c', '.cpp', '.h']
        for ext in common_extensions:
            token = ext + "/"
            if token in descriptor:
                split_idx = descriptor.rfind(token)
                if split_idx != -1:
                    descriptor = descriptor[split_idx + len(token):]
                    break
        
        # 2. Normalizzazione caratteri
        clean = descriptor.replace('`', '').replace('/', '.').replace('#', '.')
        clean = clean.replace('().', '.').replace('()', '').rstrip('.')
        while '..' in clean: clean = clean.replace('..', '.')

        # 3. Strip Module Path (Heuristic: drop leading lowercase parts)
        # Es: code_graph_indexer.graph.base.BaseGraphIndexer -> BaseGraphIndexer
        if '.' in clean:
            parts = clean.split('.')
            start_idx = 0
            for i, p in enumerate(parts):
                # Se è l'ultimo, lo teniamo (es. funzione top-level 'my_func')
                if i == len(parts) - 1: 
                    start_idx = i
                    break
                # Se inizia con Maiuscola, è una Classe/Tipo -> Inizio parte rilevante
                if p and p[0].isupper():
                    start_idx = i
                    break
                # Se è minuscolo, assumiamo sia un package/modulo -> Drop
            
            clean = ".".join(parts[start_idx:])

        return sys.intern(clean)

    def _infer_symbol_type(self, raw: str) -> str:
        """Deduce il tipo dal descrittore SCIP grezzo."""
        parts = raw.split()
        if not parts: return "unknown"
        desc = parts[-1]
        
        if desc.endswith(").") or desc.endswith(")"): return "method"
        if "(__init__)" in desc or "constructor" in desc.lower(): return "constructor"
        if desc.endswith("."): return "variable" # SCIP terms
        if "$" in desc: return "parameter"
        if desc.endswith("#"): return "class" # SCIP types
            
        return "symbol"
    
    # ... _make_desc, _lines, _bytes rimangono uguali ...
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