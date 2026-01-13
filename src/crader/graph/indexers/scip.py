import concurrent.futures
import fnmatch
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from functools import lru_cache
from typing import Dict, Generator, List, Optional, Tuple

# --- TELEMETRY IMPORTS ---
from opentelemetry import context, trace
from opentelemetry.trace import Status, StatusCode

from ...parsing.parsing_filters import GLOBAL_IGNORE_DIRS, SEMANTIC_NOISE_DIRS
from ..base import BaseGraphIndexer, CodeRelation

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)  # Initialize the tracer

# --- SCIP CONSTANTS & UTILS ---
SCIP_ROLE_DEFINITION = 1
SCIP_ROLE_REFERENCE = 8
SCIP_ROLE_READ = 16
SCIP_ROLE_WRITE = 32
SCIP_ROLE_OVERRIDE = 64
SCIP_ROLE_IMPLEMENTATION = 128


def get_relation_verb(role_mask: int) -> str:
    if role_mask & SCIP_ROLE_DEFINITION:
        return "defines"
    if role_mask & SCIP_ROLE_OVERRIDE:
        return "overrides"
    if role_mask & SCIP_ROLE_IMPLEMENTATION:
        return "implements"
    if role_mask & SCIP_ROLE_WRITE:
        return "writes_to"
    if role_mask & SCIP_ROLE_READ:
        return "reads_from"
    return "calls"


# --- 2. DISK SYMBOL TABLE ---
class DiskSymbolTable:
    """
    Ephemeral SQLite-backed Symbol Table.

    Used during the indexing phase to resolve symbol references (calls) to their definitions.

    **Why SQLite?**
    *   **Memory Efficiency**: Large repositories (monorepos) can have millions of symbols.
        Keeping a `Dict[str, Location]` in Python memory causes OOM kills.
    *   **Performance**: Bulk inserts and indexed lookups are faster than specialized disk-KV stores for this use case.

    **Lifecycle**:
    Created fresh for each indexing run and automatically deleted upon completion.
    """

    def __init__(self):
        self.db_path = tempfile.NamedTemporaryFile(delete=False, suffix=".db").name
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()
        # Performance Tuning: Speed over Durability (It's a temporary cache)
        self.cursor.execute("PRAGMA synchronous = OFF")
        self.cursor.execute("PRAGMA journal_mode = MEMORY")
        self.cursor.execute(
            "CREATE TABLE defs (symbol TEXT, scope_file TEXT, file_path TEXT, start_line INTEGER, start_char INTEGER, end_line INTEGER, end_char INTEGER, PRIMARY KEY (symbol, scope_file))"
        )
        self.buffer = []
        self._insert_count = 0  # Internal telemetry

    def add(self, symbol: str, file_path: str, scip_range: List[int], is_local: bool):
        scope = file_path if is_local else ""
        s_line, s_char = scip_range[0], scip_range[1]
        el = scip_range[2] if len(scip_range) > 3 else s_line
        ec = scip_range[3] if len(scip_range) > 3 else scip_range[2]
        self.buffer.append((symbol, scope, file_path, s_line, s_char, el, ec))
        if len(self.buffer) >= 10000:
            self.flush()

    def flush(self):
        if self.buffer:
            self._insert_count += len(self.buffer)
            self.cursor.executemany("INSERT OR REPLACE INTO defs VALUES (?, ?, ?, ?, ?, ?, ?)", self.buffer)
            self.conn.commit()
            self.buffer = []

    def get(self, symbol: str, current_file: str) -> Optional[Tuple[str, List[int]]]:
        self.cursor.execute(
            "SELECT file_path, start_line, start_char, end_line, end_char FROM defs WHERE symbol = ? AND scope_file = ?",
            (symbol, current_file),
        )
        row = self.cursor.fetchone()
        if not row:
            self.cursor.execute(
                "SELECT file_path, start_line, start_char, end_line, end_char FROM defs WHERE symbol = ? AND scope_file = ''",
                (symbol,),
            )
            row = self.cursor.fetchone()
        if row:
            return row[0], [row[1], row[2], row[3], row[4]]
        return None

    def close(self):
        self.flush()
        self.conn.close()
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except OSError:
                pass
        return self._insert_count


# --- 3. SCIP RUNNER (STREAMING) ---
class SCIPRunner:
    """
    Facade for the Source Code Indexing Protocol (SCIP) CLI tools.

    Manages the lifecycle of external indexers (scip-python, scip-typescript, etc.):
    1.  **Discovery**: Auto-detects project types based on marker files (e.g. `package.json`).
    2.  **Execution**: Runs the correct SCIP indexer in a subprocess / temp environment.
    3.  **Optimization**: Prunes the workspace before indexing to speed up traversal.
    4.  **Streaming**: Decodes the protobuf output into a Python generator.
    """

    PROJECT_MARKERS = {
        "pyproject.toml": "scip-python",
        "requirements.txt": "scip-python",
        "setup.py": "scip-python",
        "package.json": "scip-typescript",
        "tsconfig.json": "scip-typescript",
        "pom.xml": "scip-java",
        "build.gradle": "scip-java",
        "go.mod": "scip-go",
        "Cargo.toml": "scip-rust",
        "composer.json": "scip-php",
        "compile_commands.json": "scip-clang",
    }
    EXTENSION_MAP = {
        ".py": "scip-python",
        ".ts": "scip-typescript",
        ".js": "scip-typescript",
        ".java": "scip-java",
        ".go": "scip-go",
        ".rs": "scip-rust",
        ".php": "scip-php",
        ".c": "scip-clang",
        ".cpp": "scip-clang",
    }

    SCIP_CLI = "scip"

    def __init__(self, repo_path: str):
        self.repo_path = os.path.abspath(repo_path)
        self._active_indices = []
        self.ignore_rules = GLOBAL_IGNORE_DIRS | SEMANTIC_NOISE_DIRS

    def _prune_workspace(self, project_root: str):
        """
        IO Optimization: Aggressive Workspace Pruning.

        Removes all non-essential files (images, logs, binaries) from the worktree *before*
        passing it to the SCIP indexer. This drastically reduces the time SCIP tools spend
        walking the filesystem.

        **Safety**:
        Only operates on the isolated, ephemeral worktree managed by `GitVolumeManager`,
        never on the user's actual source code.
        """
        with tracer.start_as_current_span("scip.prune_workspace") as span:
            span.set_attribute("scip.prune.root", project_root)

            # Set per lookup O(1)
            targets = self.ignore_rules
            valid_exts = set(self.EXTENSION_MAP.keys())
            valid_markers = set(self.PROJECT_MARKERS.keys())

            removed_dirs = 0
            removed_files = 0


            pruned_dirs_list = []
            pruned_files_list = []

            for root, dirs, files in os.walk(project_root, topdown=True):
                # 1. Directory Pruning (Blacklist)
                dirs_to_remove = set()
                for d in dirs:
                    if d.startswith("."):
                        dirs_to_remove.add(d)
                        continue
                    if d in targets:
                        dirs_to_remove.add(d)
                        continue
                    for rule in targets:
                        if fnmatch.fnmatch(d, rule):
                            dirs_to_remove.add(d)
                            break

                for d in dirs_to_remove:
                    try:
                        full_path = os.path.join(root, d)
                        shutil.rmtree(full_path)
                        removed_dirs += 1
                        pruned_dirs_list.append(os.path.relpath(full_path, project_root))
                    except OSError:
                        pass

                # Update dirs list to stop descent
                dirs[:] = [d for d in dirs if d not in dirs_to_remove]

                # 2. File Pruning (Aggressive Whitelist) [NEW]
                """
                Cleans the workspace keeping ONLY source code and configuration files.
                Drastically reduces I/O and SCIP scanning time.
                """
                for f in files:
                    if f in valid_markers:
                        continue  # Keep package.json, requirements.txt...

                    _, ext = os.path.splitext(f)
                    if ext in valid_exts:
                        continue  # Keep .py, .ts, .java...

                    # If we get here, it's a useless file (e.g. .jpg, .csv, .log) -> DELETE
                    try:
                        full_path = os.path.join(root, f)
                        os.remove(full_path)
                        removed_files += 1
                        pruned_files_list.append(os.path.relpath(full_path, project_root))
                    except OSError:
                        pass

            span.set_attribute("scip.prune.removed_dirs", removed_dirs)
            span.set_attribute("scip.prune.removed_files", removed_files)

            if pruned_dirs_list:
                logger.info(f"✂️ [SCIP Prune] Removed Directories: {', '.join(pruned_dirs_list)}")
            if pruned_files_list:
                logger.info(f"✂️ [SCIP Prune] Removed Files: {', '.join(pruned_files_list)}")

            logger.info(f"✂️ [SCIP Prune] Cleaned {removed_dirs} dirs and {removed_files} junk files.")

    def prepare_indices(self) -> List[Tuple[str, str]]:
        with tracer.start_as_current_span("scip.prepare_indices") as span:
            if not shutil.which(self.SCIP_CLI):
                span.record_exception(FileNotFoundError("SCIP CLI not found"))
                span.set_status(Status(StatusCode.ERROR, "SCIP CLI missing"))
                logger.error(f"[SCIP] CLI '{self.SCIP_CLI}' not found.")
                return []

            tasks = self._discover_tasks()
            span.set_attribute("scip.tasks_count", len(tasks))

            if not tasks:
                logger.warning("[SCIP] No indexing tasks found.")
                return []

            results = []
            env = os.environ.copy()
            env["PYTHONPATH"] = self.repo_path + os.pathsep + env.get("PYTHONPATH", "")
            current_ctx = context.get_current()

            with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(tasks), 4)) as executor:
                future_to_task = {executor.submit(self._run_single_index, t, env, current_ctx): t for t in tasks}
                for future in concurrent.futures.as_completed(future_to_task):
                    res = future.result()
                    if res:
                        results.append(res)
                        self._active_indices.append(res[1])

            span.set_attribute("scip.successful_indices", len(results))
            return results

    def _run_single_index(self, task, env, ctx) -> Optional[Tuple[str, str]]:
        token = context.attach(ctx)
        indexer, project_root = task
        # Use the tracer to monitor each single CLI execution
        with tracer.start_as_current_span("scip.exec_cli") as span:
            span.set_attribute("scip.indexer", indexer)
            span.set_attribute("scip.project_root", project_root)

            tmp_idx = tempfile.NamedTemporaryFile(delete=False, suffix=".scip").name

            try:
                self._prune_workspace(project_root)

                logger.info(f"[SCIP] Indexing {project_root} with {indexer}...")

                # Attempt 1
                result = subprocess.run(
                    [indexer, "index", ".", "--output", tmp_idx],
                    cwd=project_root,
                    check=False,
                    capture_output=True,
                    env=env,
                    text=True,
                )

                # RETRY LOGIC for stubborn tools (like scip-python crashing on pyproject.toml)
                if result.returncode != 0 and indexer == "scip-python":
                     logger.warning(f"⚠️ [SCIP] {indexer} failed (code {result.returncode}). Retrying without pyproject.toml...")
                     pyproj = os.path.join(project_root, "pyproject.toml")
                     if os.path.exists(pyproj):
                         try:
                             os.remove(pyproj)
                         except OSError:
                             pass

                         # Attempt 2
                         result = subprocess.run(
                            [indexer, "index", ".", "--output", tmp_idx],
                            cwd=project_root,
                            check=False,
                            capture_output=True,
                            env=env,
                            text=True,
                         )

                span.set_attribute("scip.exit_code", result.returncode)

                if result.returncode != 0:
                    span.set_status(Status(StatusCode.ERROR))
                    span.set_attribute("scip.stderr", result.stderr[:2000])  # Limit buffer size
                    logger.error(f"❌ [SCIP FAIL] {indexer} exited with code {result.returncode}\nSTDERR:\n{result.stderr}")
                    return None

                if os.path.exists(tmp_idx):
                    size = os.path.getsize(tmp_idx)
                    span.set_attribute("scip.index_size_bytes", size)
                    if size > 10:
                        return (project_root, tmp_idx)

                logger.warning(f"⚠️ [SCIP WARN] Index file empty for {project_root}")
                span.add_event("empty_index_file")
                return None

            except Exception as e:
                logger.error(f"❌ [SCIP ERROR] Exception {indexer}: {e}")
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR))
                return None
            finally:
                context.detach(token)

    def stream_documents(self, indices: List[Tuple[str, str]]) -> Generator[Dict, None, None]:
        """
        Yields documents from generated SCIP indices.

        Invokes `scip print --json` to convert the binary Protobuf index into a stream of JSON objects.
        This allows processing indices that are larger than available memory.
        """
        for project_root, index_path in indices:
            # Monitor the reading/streaming process
            with tracer.start_as_current_span("scip.stream_decode") as span:
                span.set_attribute("scip.index_path", index_path)
                doc_count = 0

                try:
                    proc = subprocess.Popen(
                        [self.SCIP_CLI, "print", "--json", index_path],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        text=True,
                    )
                    for line in proc.stdout:
                        if not line.strip():
                            continue
                        try:
                            payload = json.loads(line)
                            docs = payload if isinstance(payload, list) else payload.get("documents", [payload])
                            for doc in docs:
                                if self._should_skip_document(doc.get("relative_path", "")):
                                    continue
                                doc_count += 1
                                yield {"project_root": project_root, "document": doc}
                        except ValueError:
                            pass
                    proc.wait()
                except Exception as e:
                    span.record_exception(e)
                    logger.error(f"[SCIP] Stream error for {project_root}: {e}")

                span.set_attribute("scip.docs_streamed", doc_count)

    def _should_skip_document(self, rel_path: str) -> bool:
        if not rel_path:
            return True
        parts = rel_path.split("/")
        for part in parts:
            if part in self.ignore_rules or part.startswith("."):
                return True
            for rule in self.ignore_rules:
                if fnmatch.fnmatch(part, rule):
                    return True
        return False

    def cleanup(self):
        for p in self._active_indices:
            try:
                os.remove(p)
            except:
                pass
        self._active_indices = []

    def _discover_tasks(self) -> List[Tuple[str, str]]:
        tasks = []
        found_roots = set()

        for root, dirs, files in os.walk(self.repo_path, topdown=True):
            dirs_to_skip = []
            for d in dirs:
                if d.startswith(".") or d in self.ignore_rules:
                    dirs_to_skip.append(d)
                    continue
                for rule in self.ignore_rules:
                    if fnmatch.fnmatch(d, rule):
                        dirs_to_skip.append(d)
                        break

            dirs[:] = [d for d in dirs if d not in dirs_to_skip]

            if any(root.startswith(p) for p in found_roots):
                continue

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
                        if shutil.which(idx):
                            detected.add(idx)
            for idx in detected:
                tasks.append((idx, self.repo_path))
        return tasks

    def _find_installed_indexers(self):
        return {}


# --- 4. INDEXER ---
class SCIPIndexer(BaseGraphIndexer):
    """
    Codebase Graph Builder based on LSIF/SCIP.

    This is the "Deep Analysis" engine. Unlike Tree-sitter (which is purely syntactic),
    SCIP indexers understand the semantics of the language (type resolution, cross-file references).

    **Pipeline**:
    1.  **Detection**: Determine language and tools.
    2.  **Indexing**: Run `scip-python`, `scip-java` etc. to produce `.scip` files.
    3.  **Extraction**: Two-pass processing of the index:
        *   Pass 1: Build a Symbol Table of all Definitions.
        *   Pass 2: Resolve all References (Calls) against the Definitions.
    """

    INDEXER_NAME = "scip"

    def __init__(self, repo_path: str):
        super().__init__(repo_path)
        self.repo_path = os.path.abspath(repo_path)
        self.runner = SCIPRunner(repo_path)

    def extract_relations(self, chunk_map: Dict) -> List[CodeRelation]:
        # Wrap here too for safety
        with tracer.start_as_current_span("scip.extract_relations"):
            return list(self.stream_relations())

    def stream_relations(
        self, exclude_definitions: bool = True, exclude_externals: bool = True
    ) -> Generator[CodeRelation, None, None]:
        """
        Orchestrates the Relation Extraction Pipeline.

        Yields `CodeRelation` objects representing the "Edge Configuration" of the code graph.

        Args:
            exclude_definitions: If True, do not yield edges for definitions themselves (just references).
            exclude_externals: If True, ignore calls to libraries outside the repo (e.g. stdlib).
        """
        with tracer.start_as_current_span("scip.pipeline_run") as span:
            indices = self.runner.prepare_indices()
            if not indices:
                span.set_attribute("scip.no_indices", True)
                return

            symbol_table = DiskSymbolTable()
            total_rels = 0

            try:
                # Fase 1: Definition Pass
                with tracer.start_as_current_span("scip.pass_definitions") as def_span:
                    for wrapper in self.runner.stream_documents(indices):
                        self._process_definitions(wrapper, symbol_table)

                    inserted = symbol_table.flush()  # Modified to return count if desired, or use attribute
                    # Here we access the internal variable for telemetry
                    def_span.set_attribute("scip.definitions_found", symbol_table._insert_count)

                # Fase 2: Occurrence Pass
                with tracer.start_as_current_span("scip.pass_occurrences") as occ_span:
                    for wrapper in self.runner.stream_documents(indices):
                        for rel in self._process_occurrences(
                            wrapper, symbol_table, exclude_definitions, exclude_externals
                        ):
                            yield rel
                            total_rels += 1

                    occ_span.set_attribute("scip.relations_yielded", total_rels)

            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR))
                logger.error(f"SCIP Pipeline Fatal Error: {e}")
                raise e  # Rilanciamo per il gestore superiore

            finally:
                definitions_count = symbol_table.close()  # close can now return stats
                self.runner.cleanup()
                span.set_attribute("scip.total_definitions_db", definitions_count if definitions_count else 0)
                span.set_attribute("scip.total_relations_final", total_rels)

    def _process_definitions(self, wrapper: Dict, table: DiskSymbolTable):
        root, doc = wrapper["project_root"], wrapper["document"]
        if "relative_path" in doc and root:
            abs_p = os.path.join(root, doc["relative_path"])
            norm_p = sys.intern(os.path.relpath(abs_p, self.repo_path))
            if not norm_p.startswith(".."):
                for o in doc.get("occurrences", []):
                    if o.get("symbol_roles", 0) & SCIP_ROLE_DEFINITION:
                        is_local = o["symbol"].startswith("local")
                        table.add(o["symbol"], norm_p, o["range"], is_local)

    def _process_occurrences(
        self, wrapper: Dict, table: DiskSymbolTable, exclude_definitions: bool, exclude_externals: bool
    ) -> Generator[CodeRelation, None, None]:
        # Optimization: Do not create a span for each file here, too much overhead.
        # The parent span "scip.pass_occurrences" covers everything.
        root, doc = wrapper["project_root"], wrapper["document"]
        if "relative_path" not in doc or not root:
            return

        abs_p = os.path.join(root, doc["relative_path"])
        norm_p = sys.intern(os.path.relpath(abs_p, self.repo_path))
        if norm_p.startswith(".."):
            return

        for o in doc.get("occurrences", []):
            roles = o.get("symbol_roles", 0)
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
                tgt = sys.intern(f"EXTERNAL::{parts[2]}::{parts[3]}") if len(parts) >= 4 else "EXTERNAL::UNKNOWN"
                tgt_rng = []
            else:
                continue

            if exclude_externals and ext:
                continue

            verb = get_relation_verb(roles)
            clean_sym = self._extract_symbol_name(norm_p, o["range"]) if is_local else self._clean_symbol(raw_sym)
            if not clean_sym or clean_sym == "unknown":
                continue

            yield CodeRelation(
                norm_p,
                tgt,
                verb,
                source_line=o["range"][0] + 1,
                target_line=tgt_rng[0] + 1 if not ext else 1,
                source_byte_range=self._bytes(norm_p, o["range"]),
                target_byte_range=None if ext else self._bytes(tgt, tgt_rng),
                metadata={"tool": self.INDEXER_NAME, "symbol": clean_sym, "is_external": ext},
            )

    @lru_cache(maxsize=1024)
    def _get_file_content_cached(self, rel_path: str) -> Optional[List[str]]:
        abs_path = os.path.join(self.repo_path, rel_path)
        if not os.path.exists(abs_path):
            return None
        try:
            if os.path.getsize(abs_path) > 1024 * 1024:
                return None
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.readlines()
        except:
            return None

    def _extract_symbol_name(self, rel_path: str, rng: List[int]) -> str:
        lines = self._get_file_content_cached(rel_path)
        if not lines:
            return "unknown"
        try:
            sl, sc = rng[0], rng[1]
            el, ec = (sl, rng[2]) if len(rng) == 3 else (rng[2], rng[3])
            if sl >= len(lines):
                return "unknown"
            return lines[sl][sc:ec] if sl == el else "unknown"
        except:
            return "unknown"

    def _clean_symbol(self, raw: str) -> str:
        parts = raw.split()
        if not parts:
            return sys.intern(raw)
        desc = parts[-1]
        for ext in [".py/", ".ts/", ".js/", ".java/", ".go/"]:
            if ext in desc:
                desc = desc.split(ext)[-1]
                break
        return sys.intern(desc.replace("/", ".").replace("#", ".").rstrip("."))

    @lru_cache(maxsize=64)
    def _lines(self, p):
        ap = os.path.join(self.repo_path, p)
        if not os.path.exists(ap):
            return None
        try:
            with open(ap, "rb") as f:
                return [0] + [i + 1 for i, b in enumerate(f.read()) if b == 10]
        except:
            return None

    def _bytes(self, p, rng):
        l = self._lines(p)
        if not l:
            return None
        try:
            sl = rng[0]
            el = rng[2] if len(rng) > 3 else rng[0]
            if sl >= len(l):
                return None
            return [l[sl] + rng[1], l[el] + (rng[3] if len(rng) > 3 else rng[2])]
        except:
            return None
