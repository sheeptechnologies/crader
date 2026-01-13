import datetime
import fnmatch
import hashlib
import os
import uuid
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

from opentelemetry import trace
from tree_sitter import Node, Parser

# Safe import for QueryCursor (some versions might differ or it might be missing in mocks)
try:
    from tree_sitter import QueryCursor
except ImportError:
    QueryCursor = None

try:
    from tree_sitter_languages import get_language
except ImportError:
    from tree_sitter_language_pack import get_language

from ..models import ChunkContent, ChunkNode, CodeRelation, FileRecord
from ..providers.metadata import GitMetadataProvider, LocalMetadataProvider, MetadataProvider
from .parsing_filters import (
    GLOBAL_IGNORE_DIRS,
    LANGUAGE_SPECIFIC_FILTERS,
    MAX_FILE_SIZE_BYTES,
    MAX_LINE_LENGTH,
)

tracer = trace.get_tracer(__name__)


class TreeSitterRepoParser:
    """
    High-Performance Semantic Code Parser powered by Tree-Sitter.

    This engine is responsible for converting raw source code into a structured Code Property Graph (CPG).
    It employs several optimizations for throughput and memory efficiency:

    **Key Architecture:**
    *   **Zero-Copy Slicing**: Uses Python `memoryview` and `bytearray` to handle large file contents without redundant copying.
    *   **Incremental Hashing**: Computes SHA-256 signatures for files and chunks to enable content-addressable storage (CAS) and deduplication.
    *   **Polyglot Support**: Dynamically loads Tree-Sitter grammars based on file extension.
    *   **Semantic Querying**: Uses S-expression queries (`.scm` files) to extract high-level constructs like Classes, Functions, and Imports.

    **Pipeline phase**:
    1.  **Filtering**: Selects relevant files (ignoring test/vendor noise).
    2.  **Parsing**: Builds a Concrete Syntax Tree (CST).
    3.  **Capturing**: Applies semantic queries to identify "Interesting Nodes".
    4.  **Chunking**: Recursively breaks down the code into manageable blocks ("Chunks") preserving lexical scope.
    """

    EXT_TO_LANG_CONFIG = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "javascript",
        ".tsx": "javascript",
        ".java": "java",
        ".go": "go",
        ".html": "web",
        ".css": "web",
        ".json": "web",
    }

    LANGUAGE_MAP = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".go": "go",
        ".java": "java",
        ".c": "c",
        ".cpp": "cpp",
        ".rs": "rust",
        ".html": "html",
        ".css": "css",
        ".php": "php",
    }

    MAX_CHUNK_SIZE = 800
    CHUNK_TOLERANCE = 400

    CONTAINER_TYPES = {
        "class_definition",
        "class_declaration",
        "function_definition",
        "method_definition",
        "function_declaration",
        "arrow_function",
        "interface_declaration",
        "impl_item",
        "mod_item",
        "async_function_definition",
        "decorated_definition",
        "export_statement",
    }

    GLUE_TYPES = {"comment", "decorator", "line_comment", "block_comment", "string_literal"}

    def __init__(self, repo_path: str, metadata_provider: Optional[MetadataProvider] = None):
        self.repo_path = os.path.abspath(repo_path)
        if not os.path.isdir(self.repo_path):
            raise FileNotFoundError(f"Invalid path: {repo_path}")

        is_git_repo = os.path.exists(os.path.join(self.repo_path, ".git"))
        self.metadata_provider = metadata_provider or (
            GitMetadataProvider(self.repo_path) if is_git_repo else LocalMetadataProvider(self.repo_path)
        )
        self.repo_info = self.metadata_provider.get_repo_info()

        self.snapshot_id: Optional[str] = None

        # Caricamento Lingue (Cache)
        self.languages: Dict[str, Any] = {}
        lang_cache: Dict[str, Any] = {}
        self._query_cache: Dict[str, Any] = {}

        for ext, lang_name in self.LANGUAGE_MAP.items():
            if lang_name in lang_cache:
                self.languages[ext] = lang_cache[lang_name]
                continue
            try:
                lang_obj = get_language(lang_name)
                lang_cache[lang_name] = lang_obj
                self.languages[ext] = lang_obj
            except Exception as e:
                print(f"[ERROR] Failed to load language {lang_name}: {e}")
                continue

        self.parser = Parser()

        # The parser ignores BOTH technical noise (node_modules) AND semantic noise (if configured).
        # However, in our design, the Parser ACCEPTS semantic noise (e.g., test files) but SCIP does not.
        # So here we only ignore GLOBAL_IGNORE_DIRS (technical noise).
        # If you want the parser to ignore tests as well, add SEMANTIC_NOISE_DIRS here.
        self.all_ignore_dirs = GLOBAL_IGNORE_DIRS  # | SEMANTIC_NOISE_DIRS

    def _set_parser_language(self, lang_object):
        if hasattr(self.parser, "set_language"):
            self.parser.set_language(lang_object)
        else:
            self.parser.language = lang_object

    # ==============================================================================
    #  RESILIENCE & I/O
    # ==============================================================================

    def _is_binary(self, content_sample: bytes) -> bool:
        return b"\0" in content_sample

    def _is_minified_or_generated(self, content_sample: bytes, file_path: str) -> bool:
        """
        Content-based heuristic to detect minified/generated files.

        Returns:
            bool: True if the file should be skipped.
        """
        try:
            # Check 1: Line too long (Minified JS/CSS)
            # Analyze only the first lines for speed
            first_lines = content_sample[:2048].split(b"\n")[:5]
            for line in first_lines:
                if len(line) > MAX_LINE_LENGTH:
                    return True

            # Check 2: Header "Auto-generated" comune
            header = content_sample[:500].lower()
            if b"generated by" in header or b"auto-generated" in header or b"do not edit" in header:
                return True

        except Exception:
            pass
        return False

    def _safe_read_file(self, full_path: str) -> Tuple[Optional[bytes], Optional[str]]:
        try:
            size = os.path.getsize(full_path)
            if size > MAX_FILE_SIZE_BYTES:
                return None, f"File too large ({size / 1024 / 1024:.2f} MB)"

            with open(full_path, "rb") as f:
                head = f.read(1024)
                if self._is_binary(head):
                    return None, "Binary file detected"

                f.seek(0)
                content = f.read()
                return content, None
        except Exception as e:
            return None, f"Read Error: {str(e)}"

    # ==============================================================================
    #  SEMANTIC QUERY ENGINE
    # ==============================================================================

    def _load_query_for_language(self, language_name: str) -> Optional[str]:
        """Loads the raw S-expression query string for the given language from disk."""
        try:
            base_path = os.path.dirname(__file__)
            query_path = os.path.join(base_path, "queries", f"{language_name}.scm")
            if os.path.exists(query_path):
                with open(query_path, "r", encoding="utf-8") as f:
                    return f.read()
        except Exception:
            # Silent if the query file is missing for less common languages
            pass
        return None

    def _generate_label(self, category: str, value: str) -> str:
        """Generates a human-readable label for a semantic capture (e.g., 'class' -> 'Class Definition')."""
        labels = {
            ("role", "entry_point"): "Application Entry Point",
            ("role", "test_suite"): "Test Suite Class",
            ("role", "test_case"): "Unit/Integration Test Case",
            ("role", "api_endpoint"): "API Route Handler",
            ("role", "data_schema"): "Data Model / Schema",
            ("type", "class"): "Class Definition",
            ("type", "function"): "Function Definition",
        }
        return labels.get((category, value), f"{value.replace('_', ' ').title()}")

    def _get_semantic_captures(self, tree, language_name: str) -> List[Dict[str, Any]]:
        # 1. CHECK CACHE (Fast Path)
        if language_name in self._query_cache:
            query = self._query_cache[language_name]
        else:
            # 2. LOAD & COMPILE (Slow Path - Only once per worker)
            query_scm = self._load_query_for_language(language_name)
            if not query_scm:
                self._query_cache[language_name] = None
                return []

            target_ext = next((ext for ext, lang in self.LANGUAGE_MAP.items() if lang == language_name), None)
            if not target_ext or target_ext not in self.languages:
                self._query_cache[language_name] = None
                return []

            lang_obj = self.languages[target_ext]

            try:
                # Compilation is expensive, we do it only once
                query = lang_obj.query(query_scm)
                self._query_cache[language_name] = query
            except Exception as e:
                print(f"[ERROR] Invalid query for {language_name}: {e}")
                self._query_cache[language_name] = None
                return []

        # If the query does not exist or failed previously, exit
        if query is None:
            return []

        try:
            # 3. EXECUTE (C-Speed)
            # Supporto per tree-sitter >= 0.22 (usa QueryCursor)
            if QueryCursor:
                cursor = QueryCursor(query)
                captures = cursor.captures(tree.root_node)
            else:
                # Legacy support
                captures = query.captures(tree.root_node)

            # Normalize dictionary results (New API) to list of tuples (Old Logic)
            if isinstance(captures, dict):
                flat_captures = []
                for name, nodes_list in captures.items():
                    # captures dict values can be single nodes or lists?
                    # Test output showed list: {'m': [<Node ...>]}
                    if not isinstance(nodes_list, list):
                        nodes_list = [nodes_list]
                    for n in nodes_list:
                        flat_captures.append((n, name))
                captures = flat_captures

            results = []

            # Ottimizzazione Loop: riduciamo lookup e split
            for node, capture_name in captures:
                # print(f"[DEBUG] Capture: {capture_name} at {node.start_byte}-{node.end_byte}")

                # capture_name è es: "role.class"
                if "." not in capture_name:
                    continue

                category, value = capture_name.split(".", 1)

                results.append(
                    {
                        "start": node.start_byte,
                        "end": node.end_byte,
                        "metadata": {
                            "category": category,
                            "value": value,
                            # Memo: _generate_label è veloce, ma potremmo cachare anche lui se servisse
                            "label": self._generate_label(category, value),
                        },
                    }
                )

            # print(f"[DEBUG] Found {len(results)} matches for {language_name}")
            return results
        except Exception as e:
            print(f"[ERROR] Capture error for {language_name}: {e}")
            return []

    # ==============================================================================
    #  MAIN PIPELINE (OPTIMIZED)
    # ==============================================================================

    def stream_semantic_chunks(
        self, file_list: Optional[List[str]] = None
    ) -> Generator[Tuple[FileRecord, List[ChunkNode], List[ChunkContent], List[CodeRelation]], None, None]:
        """
        The Main Pipeline Entry Point.

        Iterates over the provided file list (or scans the repo) and yields structured graph data.
        It is designed to be a generator to allow streaming processing and keep memory usage constant.

        **Yields**:
            A tuple containing:
            1.  `FileRecord`: Metadata and status of the file.
            2.  `List[ChunkNode]`: All the chunks (functions, classes) found.
            3.  `List[ChunkContent]`: The actual text content (for CAS).
            4.  `List[CodeRelation]`: Relationships (parent-child) extracted.

        Args:
            file_list (List[str], optional): Explicit list of relative paths to process.
        """
        if not self.snapshot_id:
            raise ValueError("Parser: snapshot_id not set.")

        commit_hash = self.repo_info.get("commit_hash", "HEAD")

        # Iterazione diretta sulla lista fornita (O(N))
        for rel_path in file_list:
            full_path = os.path.join(self.repo_path, rel_path)

            # Check di sicurezza base: il file deve esistere (il chiamante potrebbe aver listato file cancellati)
            if not os.path.isfile(full_path):
                continue

            filename = os.path.basename(rel_path)
            _, ext = os.path.splitext(filename)

            # Fast check: Abbiamo il supporto per questa estensione?
            # Nota: L'indexer dovrebbe aver già filtrato, ma questo è un check a costo zero.
            lang_object = self.languages.get(ext)
            if not lang_object:
                continue

            # [REMOVED] _should_process_file: L'indexer ha già deciso che questo file va processato.
            # Se vogliamo essere difensivi possiamo lasciarlo, ma per massima velocità ci fidiamo dell'input.
            # Se decidi di lasciarlo per sicurezza:
            # if not self._should_process_file(rel_path): continue

            # [OTEL] Span per singolo file.
            # Questo è fondamentale per debugging granulare.
            with tracer.start_as_current_span("parser.process_file") as span:
                span.set_attribute("file.path", rel_path)
                span.set_attribute("file.extension", ext)
                span.set_attribute("file.lang", self.LANGUAGE_MAP[ext])

                try:
                    # 1. READ (I/O)
                    with tracer.start_as_current_span("parser.io_read") as io_span:
                        content, error_msg = self._safe_read_file(full_path)
                        if content:
                            io_span.set_attribute("file.size_bytes", len(content))

                    # Gestione Errori Lettura / Minificazione
                    if error_msg or self._is_minified_or_generated(content, rel_path):
                        span.set_attribute("parsing.status", "skipped")
                        span.set_attribute("parsing.skip_reason", error_msg or "minified")

                        # Creiamo record Skipped
                        file_rec = self._create_file_record(
                            rel_path, commit_hash, ext, status="skipped", error=error_msg or "Minified/Generated"
                        )
                        yield (file_rec, [], [], [])
                        continue

                    # 2. HASHING (CPU)
                    with tracer.start_as_current_span("parser.hashing"):
                        file_hash = self.metadata_provider.get_file_hash(rel_path, content)

                    # Creiamo record Base
                    file_rec = self._create_file_record(
                        rel_path, commit_hash, ext, size=len(content), file_hash=file_hash
                    )

                    # 3. PARSING (CPU - TreeSitter)
                    self._set_parser_language(lang_object)
                    with tracer.start_as_current_span("parser.tree_sitter"):
                        tree = self.parser.parse(content)

                    lang_name = self.LANGUAGE_MAP[ext]
                    with tracer.start_as_current_span("parser.queries_exec") as query_span:
                        semantic_captures = self._get_semantic_captures(tree, lang_name)

                    nodes = []
                    contents = {}
                    relations = []
                    mv_content = memoryview(content)

                    # 4. CHUNKING (CPU - Recursive)
                    # Nota: Un solo span per tutto l'albero, non ricorsivo
                    with tracer.start_as_current_span("parser.chunking") as chunk_span:
                        self._process_scope(
                            tree.root_node,
                            mv_content,
                            content,
                            rel_path,
                            file_rec.id,
                            None,
                            nodes,
                            contents,
                            relations,
                            semantic_captures=semantic_captures,
                        )
                        chunk_span.set_attribute("chunks.generated", len(nodes))

                    if nodes:
                        nodes.sort(key=lambda c: c.byte_range[0])
                        yield (file_rec, nodes, list(contents.values()), relations)
                    else:
                        yield (file_rec, [], [], [])

                except Exception as e:
                    # [OTEL] Capture Exception
                    span.record_exception(e)
                    span.set_status(trace.Status(trace.StatusCode.ERROR))
                    print(f"[ERROR] Processing {rel_path}: {e}")

                    # Yield Error Record
                    err_rec = self._create_file_record(rel_path, commit_hash, ext, status="failed", error=str(e))
                    yield (err_rec, [], [], [])

    def _create_file_record(self, path, commit, ext, status="success", error=None, size=0, file_hash=""):
        """Helper per pulire il codice principale"""
        return FileRecord(
            id=str(uuid.uuid4()),
            snapshot_id=self.snapshot_id,
            commit_hash=commit,
            file_hash=file_hash,
            path=path,
            language=self.LANGUAGE_MAP.get(ext, "unknown"),
            size_bytes=size,
            category=self.metadata_provider.get_file_category(path),
            indexed_at=datetime.datetime.utcnow().isoformat() + "Z",
            parsing_status=status,
            parsing_error=error,
        )

    # def extract_semantic_chunks(self) -> ParsingResult:
    #     files, nodes, contents, all_rels = [], [], {}, []
    #     for f, n, c, r in self.stream_semantic_chunks():
    #         files.append(f); nodes.extend(n); all_rels.extend(r)
    #         for item in c: contents[item.chunk_hash] = item
    #     return ParsingResult(files, nodes, contents, all_rels)

    def _should_process_file(self, rel_path: str) -> bool:
        """
        Enterprise Filtering Logic.

        Determines if a file is suitable for indexing based on:
        1.  **Global Blacklists**: `node_modules`, `.git`, temporary folders.
        2.  **Language Rules**: Allowed extensions, specific exclude patterns (e.g., `_test.go`).
        3.  **Heuristics**: Dotfiles, lockfiles.

        Returns:
            bool: True if the file should be parsed.
        """
        parts = rel_path.split(os.sep)
        filename = parts[-1]
        _, ext = os.path.splitext(filename)

        # 1. Fast Directory Check (O(1) lookup)
        # Controlla se una qualsiasi directory genitore è nella blacklist
        for part in parts[:-1]:
            if part in self.all_ignore_dirs or part.startswith("."):
                return False

        # 2. Configurazione Specifica Linguaggio
        lang_key = self.EXT_TO_LANG_CONFIG.get(ext)
        if lang_key:
            config = LANGUAGE_SPECIFIC_FILTERS[lang_key]

            # Check Estensioni proibite (es. .pyc, .min.js)
            if ext in config.get("exclude_extensions", set()):
                return False

            # Check Patterns (Glob matching, es. *_test.py)
            for pattern in config.get("exclude_patterns", []):
                if fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(rel_path, pattern):
                    # Se è un pattern escluso (es. test), decidiamo se il parser lo vuole o no.
                    # Nel design discusso: Parser VUOLE i test (Semantic Context), SCIP no.
                    # Quindi qui ritorniamo True (o meglio, non ritorniamo False).
                    # SE invece vuoi che il parser ignori i test, scommenta:
                    # return False
                    pass

        # 3. Check Generici (Lockfiles, Dotfiles nascosti)
        if filename.startswith(".") or filename.endswith(".lock"):
            return False

        return True

    # ==============================================================================
    #  LOGICA CHUNKING (OPTIMIZED - ZERO COPY & BYTEARRAY)
    # ==============================================================================

    def _process_scope(
        self,
        parent_node: Node,
        content_mv: memoryview,
        full_content_bytes: bytes,
        file_path: str,
        file_id: str,
        parent_chunk_id: Optional[str],
        nodes: List,
        contents: Dict,
        relations: List,
        initial_glue: Union[bytes, bytearray] = b"",
        initial_glue_start: Optional[int] = None,
        is_breakdown_mode: bool = False,
        semantic_captures: List[Dict] = None,
    ):
        """
        Recursive Chunking Engine.

        This is the core algorithm that iterates over the Tree-Sitter AST to group code into semantic chunks.
        It balances two competing goals:
        1.  **Granularity**: Keeping chunks small enough for embedding models (e.g. < 800 tokens).
        2.  **Context**: Keeping related code (e.g. comments + function signatures) together.

        **Algorithm**:
        *   Iterates over children of the current node.
        *   Accumulates "Glue" (comments, whitespace, small statements).
        *   When a "Barrier" (Class, Function) is met, flushes the glue and descends into the barrier.
        *   If a node is too large, it triggers `_handle_large_node` to break it down further.
        """

        semantic_captures = semantic_captures or []
        body_node = (
            parent_node.child_by_field_name("body")
            or parent_node.child_by_field_name("block")
            or parent_node.child_by_field_name("consequence")
        )
        iterator_node = body_node if body_node else parent_node

        cursor = iterator_node.start_byte

        # [OPTIMIZATION] bytearray è mutabile: append veloce senza reallocazione stringa
        glue_buffer = bytearray(initial_glue)
        glue_start_byte = initial_glue_start
        if initial_glue and glue_start_byte is None:
            glue_start_byte = iterator_node.start_byte

        group_buffer = bytearray()
        group_start_byte = None
        group_end_byte = None
        current_active_parent = parent_chunk_id
        first_chunk_created_in_scope = False

        def register_chunk_creation(new_chunk_id: str):
            nonlocal current_active_parent, first_chunk_created_in_scope
            if is_breakdown_mode and not first_chunk_created_in_scope:
                current_active_parent = new_chunk_id
                first_chunk_created_in_scope = True

        def flush_group():
            nonlocal group_buffer, group_start_byte, group_end_byte
            if group_buffer:
                cid = self._create_chunk(
                    group_buffer,
                    group_start_byte,
                    group_end_byte,
                    full_content_bytes,
                    file_path,
                    file_id,
                    current_active_parent,
                    nodes,
                    contents,
                    relations,
                    semantic_captures=semantic_captures,
                )
                register_chunk_creation(cid)
                # Reset buffer veloce
                group_buffer = bytearray()
                group_start_byte = None
                group_end_byte = None

        for child in iterator_node.children:
            if child.start_byte > cursor:
                # [OPTIMIZATION] Zero-copy slice
                gap = content_mv[cursor : child.start_byte]
                glue_buffer.extend(gap)
                if glue_start_byte is None:
                    glue_start_byte = cursor

            is_glue = child.type in self.GLUE_TYPES or child.type.startswith("comment")
            is_barrier = child.type in self.CONTAINER_TYPES

            # [OPTIMIZATION] Zero-copy slice
            child_mv = content_mv[child.start_byte : child.end_byte]

            if is_glue:
                glue_buffer.extend(child_mv)
                if glue_start_byte is None:
                    glue_start_byte = child.start_byte

            elif is_barrier:
                flush_group()

                barrier_start = glue_start_byte if glue_start_byte is not None else child.start_byte
                barrier_end = child.end_byte

                # Check dimensione approssimativa (somma lunghezze)
                full_len = len(glue_buffer) + len(child_mv)

                if full_len > self.MAX_CHUNK_SIZE:
                    # Necessario convertire in bytes immutabili solo per breakdown ricorsivo
                    prefix_bytes = bytes(glue_buffer)
                    self._handle_large_node(
                        child,
                        content_mv,
                        full_content_bytes,
                        prefix_bytes,
                        barrier_start,
                        file_path,
                        file_id,
                        current_active_parent,
                        nodes,
                        contents,
                        relations,
                        semantic_captures=semantic_captures,
                    )
                    if is_breakdown_mode and not first_chunk_created_in_scope:
                        first_chunk_created_in_scope = True
                else:
                    # Fast path: uniamo nel bytearray
                    combined = bytearray(glue_buffer)
                    combined.extend(child_mv)

                    cid = self._create_chunk(
                        combined,
                        barrier_start,
                        barrier_end,
                        full_content_bytes,
                        file_path,
                        file_id,
                        current_active_parent,
                        nodes,
                        contents,
                        relations,
                        tags=self._extract_tags(child),
                        semantic_captures=semantic_captures,
                    )
                    register_chunk_creation(cid)

                glue_buffer = bytearray()
                glue_start_byte = None

            else:
                if not group_buffer:
                    group_start_byte = glue_start_byte if glue_start_byte is not None else child.start_byte

                if glue_buffer:
                    group_buffer.extend(glue_buffer)
                    glue_buffer = bytearray()
                    glue_start_byte = None

                group_buffer.extend(child_mv)
                group_end_byte = child.end_byte

                if len(group_buffer) > self.MAX_CHUNK_SIZE:
                    remaining = iterator_node.end_byte - child.end_byte
                    if remaining > self.CHUNK_TOLERANCE:
                        flush_group()

            cursor = child.end_byte

        flush_group()

        if cursor < iterator_node.end_byte:
            glue_buffer.extend(content_mv[cursor : iterator_node.end_byte])

        if glue_buffer:
            # Check veloce su bytearray
            if not bytes(glue_buffer).strip():
                return

            start = glue_start_byte if glue_start_byte is not None else cursor
            self._create_chunk(
                glue_buffer,
                start,
                iterator_node.end_byte,
                full_content_bytes,
                file_path,
                file_id,
                current_active_parent,
                nodes,
                contents,
                relations,
                tags=[],
                semantic_captures=semantic_captures,
            )

    def _handle_large_node(
        self,
        node: Node,
        content_mv: memoryview,
        full_content_bytes: bytes,
        prefix: bytes,
        prefix_start: int,
        file_path: str,
        file_id: str,
        parent_chunk_id: Optional[str],
        nodes: List,
        contents: Dict,
        relations: List,
        semantic_captures: List[Dict] = None,
    ):
        """
        Strategy for handling nodes that exceed the chunk size limit.

        It attempts to separate the "Header" (signature, decorators) from the "Body".
        If the body is still too large, it recursively calls `_process_scope` on the body.
        As a last resort (fallback), it performs a hard lexical split.
        """

        target_node = node
        if node.type == "decorated_definition":
            definition = node.child_by_field_name("definition")
            if definition:
                target_node = definition
        elif node.type == "export_statement":
            d = node.child_by_field_name("declaration") or node.child_by_field_name("value")
            if d:
                target_node = d

        body_node = target_node.child_by_field_name("body") or target_node.child_by_field_name("block")

        if not body_node:
            # Fallback: Hard split su bytes
            full_text_bytes = prefix + bytes(content_mv[node.start_byte : node.end_byte])
            self._create_hard_split(
                full_text_bytes,
                prefix_start,
                full_content_bytes,
                file_path,
                file_id,
                parent_chunk_id,
                nodes,
                contents,
                relations,
                semantic_captures,
            )
            return

        header_node_mv = content_mv[node.start_byte : body_node.start_byte]
        full_header_len = len(prefix) + len(header_node_mv)

        if full_header_len > self.MAX_CHUNK_SIZE * 0.6:
            # Header Chunk separato
            header_buffer = bytearray(prefix)
            header_buffer.extend(header_node_mv)

            header_id = self._create_chunk(
                header_buffer,
                prefix_start,
                body_node.start_byte,
                full_content_bytes,
                file_path,
                file_id,
                parent_chunk_id,
                nodes,
                contents,
                relations,
                tags=self._extract_tags(node),
                semantic_captures=semantic_captures,
            )
            self._process_scope(
                target_node,
                content_mv,
                full_content_bytes,
                file_path,
                file_id,
                header_id,
                nodes,
                contents,
                relations,
                semantic_captures=semantic_captures,
            )
        else:
            # Flow-Down: Uniamo prefisso e header per il prossimo scope
            new_glue = bytearray(prefix)
            new_glue.extend(header_node_mv)

            self._process_scope(
                target_node,
                content_mv,
                full_content_bytes,
                file_path,
                file_id,
                parent_chunk_id,
                nodes,
                contents,
                relations,
                initial_glue=new_glue,
                initial_glue_start=prefix_start,
                is_breakdown_mode=True,
                semantic_captures=semantic_captures,
            )

    def _create_hard_split(
        self,
        text_bytes: bytes,
        start_offset: int,
        full_content: bytes,
        fpath: str,
        fid: str,
        pid: str,
        nodes: List,
        contents: Dict,
        relations: List,
        semantic_captures: List[Dict] = None,
    ):
        total = len(text_bytes)
        cursor = 0
        first_fragment_id = None
        while cursor < total:
            end = min(cursor + self.MAX_CHUNK_SIZE, total)
            if end < total:
                nl = text_bytes.rfind(b"\n", cursor, end)
                if nl > cursor + (self.MAX_CHUNK_SIZE // 2):
                    end = nl + 1
            chunk = text_bytes[cursor:end]
            current_pid = pid if not first_fragment_id else first_fragment_id
            cid = self._create_chunk(
                chunk,
                start_offset + cursor,
                start_offset + end,
                full_content,
                fpath,
                fid,
                current_pid,
                nodes,
                contents,
                relations,
                tags=[],
                semantic_captures=semantic_captures,
            )
            if not first_fragment_id:
                first_fragment_id = cid
            cursor = end

    def _create_chunk(
        self,
        text_obj: Union[bytes, bytearray, memoryview],
        s_byte: int,
        e_byte: int,
        full_content_bytes: bytes,
        fpath: str,
        fid: str,
        pid: Optional[str],
        nodes: List,
        contents: Dict,
        relations: List,
        tags: List[str] = None,
        semantic_captures: List[Dict] = None,
    ) -> Optional[str]:
        # Conversione finale solo quando serve
        if isinstance(text_obj, memoryview):
            text_bytes = text_obj.tobytes()
        elif isinstance(text_obj, bytearray):
            text_bytes = bytes(text_obj)
        else:
            text_bytes = text_obj

        text = text_bytes.decode("utf-8", "ignore")
        if not text.strip():
            return None

        # Hashing
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if h not in contents:
            contents[h] = ChunkContent(h, text)
        cid = str(uuid.uuid4())

        if s_byte < 0:
            s_byte = 0

        # Calcolo righe ottimizzato (uso full_content_bytes originale)
        s_line = full_content_bytes[:s_byte].count(b"\n") + 1
        e_line = s_line + text.count("\n")

        # Semantic Enrichment
        matches = []
        if semantic_captures:
            for cap in semantic_captures:
                cap_start, cap_end = cap["start"], cap["end"]
                if (s_byte >= cap_start and e_byte <= cap_end) or (cap_start >= s_byte and cap_end <= e_byte):
                    matches.append(cap["metadata"])

        metadata = {}
        if matches:
            metadata["semantic_matches"] = matches
        if tags:
            metadata["tags"] = tags

        nodes.append(ChunkNode(cid, fid, fpath, h, s_line, e_line, [s_byte, e_byte], metadata=metadata))

        if pid:
            relations.append(
                CodeRelation(
                    source_file=fpath,
                    target_file=fpath,
                    relation_type="child_of",
                    source_id=cid,
                    target_id=pid,
                    metadata={"tool": "treesitter_repo_parser"},
                )
            )

        return cid

    def _extract_tags(self, child: Node) -> List[str]:
        tags: List[str] = []
        if child.type.startswith("async_") or any(c.type == "async" for c in child.children):
            tags.append("async")
        if child.type == "decorated_definition" or any(c.type == "decorator" for c in child.children):
            tags.append("decorated")
        if child.type == "export_statement" or (child.parent and child.parent.type == "export_statement"):
            tags.append("exported")
        if "constructor" in child.type:
            tags.append("constructor")
        for c in child.children:
            if c.type == "static":
                tags.append("static")
        return list(set(tags))
