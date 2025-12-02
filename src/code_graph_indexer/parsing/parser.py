import os
import uuid
import hashlib
import datetime
from typing import List, Dict, Optional, Generator, Tuple, Any

from tree_sitter import Parser, Node
from tree_sitter_languages import get_language

from ..models import FileRecord, ChunkNode, ChunkContent, ParsingResult, CodeRelation
from ..providers.metadata import MetadataProvider, GitMetadataProvider, LocalMetadataProvider

class TreeSitterRepoParser:
    LANGUAGE_MAP = {
        ".py": "python", ".js": "javascript", ".jsx": "javascript", 
        ".ts": "typescript", ".tsx": "typescript", ".go": "go", 
        ".java": "java", ".c": "c", ".cpp": "cpp", ".rs": "rust",
        ".html": "html", ".css": "css", ".php": "php"
    }
    
    IGNORE_DIRS = {
        ".git", "node_modules", "target", "build", "dist", 
        ".venv", "venv", "env", ".env", "__pycache__", ".idea", ".vscode",
        "site-packages", "bin", "obj", "lib", "include", "eggs", ".eggs",
        "vendor", "bower_components", "jspm_packages"
    }

    IGNORE_FILE_SUFFIXES = {
        ".min.js", ".min.css", ".bundle.js", ".bundle.css",
        ".map", ".test.js", ".spec.js", "-lock.json", ".lock"
    }

    MAX_CHUNK_SIZE = 800 
    CHUNK_TOLERANCE = 400

    # Tipi che interrompono il flusso (Containers)
    CONTAINER_TYPES = {
        "class_definition", "class_declaration", "function_definition", "method_definition", 
        "function_declaration", "arrow_function", "interface_declaration", "impl_item", "mod_item",
        "async_function_definition", "decorated_definition", "export_statement"
    }

    GLUE_TYPES = {"comment", "decorator", "line_comment", "block_comment", "string_literal"}

    def __init__(self, repo_path: str, metadata_provider: Optional[MetadataProvider] = None):
        self.repo_path = os.path.abspath(repo_path)
        if not os.path.isdir(self.repo_path): raise FileNotFoundError(f"Invalid path: {repo_path}")

        is_git_repo = os.path.exists(os.path.join(self.repo_path, ".git"))
        self.metadata_provider = metadata_provider or (
            GitMetadataProvider(self.repo_path) if is_git_repo else LocalMetadataProvider(self.repo_path)
        )
        self.repo_info = self.metadata_provider.get_repo_info()
        self.repo_id = self.repo_info.get('repo_id', str(uuid.uuid4()))
        
        self.languages: Dict[str, Any] = {}
        lang_cache: Dict[str, Any] = {}
        for ext, lang_name in self.LANGUAGE_MAP.items():
            if lang_name in lang_cache:
                self.languages[ext] = lang_cache[lang_name]
                continue
            try:
                lang_obj = get_language(lang_name)
                lang_cache[lang_name] = lang_obj
                self.languages[ext] = lang_obj
            except Exception: continue

        self.parser = Parser()

    def _set_parser_language(self, lang_object):
        if hasattr(self.parser, 'set_language'): self.parser.set_language(lang_object)
        else: self.parser.language = lang_object

    def _is_minified_or_generated(self, content: bytes, file_path: str) -> bool:
        path_lower = file_path.lower()
        if any(path_lower.endswith(suffix) for suffix in self.IGNORE_FILE_SUFFIXES): return True
        try:
            sample = content[:4096]
            if b'\n' not in sample and len(sample) > 1000: return True
            for line in sample.split(b'\n'):
                if len(line) > 2500: return True
        except: pass
        return False
    
    # ==============================================================================
    #  SEMANTIC QUERY ENGINE
    # ==============================================================================

    def _load_query_for_language(self, language_name: str) -> Optional[str]:
        """Carica il file .scm dalla cartella 'queries'."""
        try:
            # Costruisce il path assoluto verso src/code_graph_indexer/parsing/queries/<lang>.scm
            base_path = os.path.dirname(__file__) 
            query_path = os.path.join(base_path, "queries", f"{language_name}.scm")
            
            if os.path.exists(query_path):
                with open(query_path, "r", encoding="utf-8") as f:
                    return f.read()
        except Exception as e:
            print(f"[WARN] Errore caricamento query per {language_name}: {e}")
        return None

    def _generate_label(self, category: str, value: str) -> str:
        """Genera un'etichetta leggibile per i metadati."""
        # Mappa di label predefinite per rendere l'output più pulito
        labels = {
            ("role", "entry_point"): "Application Entry Point",
            ("role", "test_suite"): "Test Suite Class",
            ("role", "test_case"): "Unit/Integration Test Case",
            ("role", "api_endpoint"): "API Route Handler",
            ("role", "data_schema"): "Data Model / Schema",
            ("type", "class"): "Class Definition",
            ("type", "function"): "Function Definition",
        }
        # Fallback generico: "entry_point" -> "Entry Point"
        return labels.get((category, value), f"{value.replace('_', ' ').title()}")

    def _get_semantic_captures(self, tree, language_name: str) -> List[Dict[str, Any]]:
        """
        Esegue le query Tree-sitter e ritorna una lista di catture semantiche.
        Filtra automaticamente le catture di servizio (senza punto).
        """
        query_scm = self._load_query_for_language(language_name)
        if not query_scm: return []
        
        target_ext = next((ext for ext, lang in self.LANGUAGE_MAP.items() if lang == language_name), None)
        if not target_ext or target_ext not in self.languages: return []
        lang_obj = self.languages[target_ext]
        
        try:
            query = lang_obj.query(query_scm)
            captures = query.captures(tree.root_node)
            
            results = []
            for node, capture_name in captures:
                # Expect format: "category.value" (es. "role.entry_point")
                parts = capture_name.split('.')
                
                # [FIX] Ignoriamo le catture interne usate per i predicati (es. @name, @val, @attr)
                # Se non contiene un punto, non è un tag semantico ufficiale.
                if len(parts) < 2:
                    continue
                
                category, value = parts[0], parts[1]
                
                results.append({
                    "start": node.start_byte,
                    "end": node.end_byte,
                    "metadata": {
                        "category": category,
                        "value": value,
                        "label": self._generate_label(category, value)
                    }
                })
            return results

        except Exception as e:
            print(f"[WARN] Semantic Query Error ({language_name}): {e}")
            return []
    
    def stream_semantic_chunks(self, file_list: Optional[List[str]] = None) -> Generator[Tuple[FileRecord, List[ChunkNode], List[ChunkContent], List[CodeRelation]], None, None]:
        files_to_process = set(file_list) if file_list else None
        
        for root, dirs, files in os.walk(self.repo_path, topdown=True):
            dirs[:] = [d for d in dirs if d.lower() not in self.IGNORE_DIRS]
            
            for file_name in files:
                _, ext = os.path.splitext(file_name)
                lang_object = self.languages.get(ext)
                if not lang_object: continue
                
                full_path = os.path.join(root, file_name)
                rel_path = os.path.relpath(full_path, self.repo_path)
                if files_to_process and rel_path not in files_to_process: continue

                try:
                    with open(full_path, 'rb') as f: content = f.read()
                    if self._is_minified_or_generated(content, rel_path): continue

                    file_rec = FileRecord(
                        id=str(uuid.uuid4()), repo_id=self.repo_id, commit_hash=self.repo_info.get('commit_hash', 'HEAD'),
                        file_hash=self.metadata_provider.get_file_hash(rel_path, content), path=rel_path,
                        language=self.LANGUAGE_MAP[ext], size_bytes=len(content),
                        category=self.metadata_provider.get_file_category(rel_path),
                        indexed_at=datetime.datetime.utcnow().isoformat() + "Z"
                    )

                    self._set_parser_language(lang_object)
                    tree = self.parser.parse(content)
                    
                    lang_name = self.LANGUAGE_MAP[ext]
                    semantic_captures = self._get_semantic_captures(tree, lang_name)
                    
                    nodes = []; contents = {}; relations = []
                    
                    self._process_scope(
                        tree.root_node, content, rel_path, file_rec.id, None, 
                        nodes, contents, relations,
                        semantic_captures=semantic_captures
                    )

                    if nodes:
                        nodes.sort(key=lambda c: c.byte_range[0])
                        yield (file_rec, nodes, list(contents.values()), relations)
                except Exception as e: 
                    print(f"[ERROR] Processing {rel_path}: {e}")

    def extract_semantic_chunks(self) -> ParsingResult:
        files, nodes, contents, all_rels = [], [], {}, []
        for f, n, c, r in self.stream_semantic_chunks():
            files.append(f); nodes.extend(n); all_rels.extend(r)
            for item in c: contents[item.chunk_hash] = item
        return ParsingResult(files, nodes, contents, all_rels)

    # --- CHUNKING LOGIC ---

    def _process_scope(self, parent_node: Node, content: bytes, file_path: str, file_id: str, parent_chunk_id: Optional[str],
                       nodes: List, contents: Dict, relations: List, 
                       initial_glue: bytes = b"", initial_glue_start: Optional[int] = None,
                       is_breakdown_mode: bool = False,
                       semantic_captures: List[Dict] = None):
        
        semantic_captures = semantic_captures or []
        body_node = parent_node.child_by_field_name("body") or parent_node.child_by_field_name("block") or parent_node.child_by_field_name("consequence")
        iterator_node = body_node if body_node else parent_node
        
        cursor = iterator_node.start_byte
        glue_buffer_bytes = initial_glue 
        glue_start_byte = initial_glue_start 
        if initial_glue and glue_start_byte is None:
            glue_start_byte = iterator_node.start_byte 

        group_buffer_bytes = b""; group_start_byte = None; group_end_byte = None
        current_active_parent = parent_chunk_id
        first_chunk_created_in_scope = False

        def register_chunk_creation(new_chunk_id: str):
            nonlocal current_active_parent, first_chunk_created_in_scope
            if is_breakdown_mode and not first_chunk_created_in_scope:
                current_active_parent = new_chunk_id
                first_chunk_created_in_scope = True

        def flush_group():
            nonlocal group_buffer_bytes, group_start_byte, group_end_byte
            if group_buffer_bytes:
                cid = self._create_chunk(
                    group_buffer_bytes, group_start_byte, group_end_byte, content,
                    file_path, file_id, current_active_parent,
                    nodes, contents, relations, semantic_captures=semantic_captures
                )
                register_chunk_creation(cid)
                group_buffer_bytes = b""; group_start_byte = None; group_end_byte = None

        for child in iterator_node.children:
            if child.start_byte > cursor:
                gap = content[cursor : child.start_byte]
                glue_buffer_bytes += gap
                if glue_start_byte is None: glue_start_byte = cursor

            is_glue = (child.type in self.GLUE_TYPES or child.type.startswith('comment'))
            is_barrier = (child.type in self.CONTAINER_TYPES)
            
            child_bytes = content[child.start_byte : child.end_byte]
            
            if is_glue:
                glue_buffer_bytes += child_bytes
                if glue_start_byte is None: glue_start_byte = child.start_byte
            
            elif is_barrier:
                flush_group()
                full_barrier_text = glue_buffer_bytes + child_bytes
                barrier_start = glue_start_byte if glue_start_byte is not None else child.start_byte
                barrier_end = child.end_byte
                
                if len(full_barrier_text) > self.MAX_CHUNK_SIZE:
                    self._handle_large_node(
                        child, content, glue_buffer_bytes, barrier_start, file_path, file_id, current_active_parent,
                        nodes, contents, relations, semantic_captures=semantic_captures
                    )
                    if is_breakdown_mode and not first_chunk_created_in_scope:
                         first_chunk_created_in_scope = True
                else:
                    cid = self._create_chunk(
                        full_barrier_text, barrier_start, barrier_end, content,
                        file_path, file_id, current_active_parent,
                        nodes, contents, relations, semantic_captures=semantic_captures
                    )
                    register_chunk_creation(cid)
                
                glue_buffer_bytes = b""; glue_start_byte = None

            else:
                if group_buffer_bytes == b"":
                    group_start_byte = glue_start_byte if glue_start_byte is not None else child.start_byte
                
                group_buffer_bytes += glue_buffer_bytes
                glue_buffer_bytes = b""; glue_start_byte = None
                group_buffer_bytes += child_bytes
                group_end_byte = child.end_byte
                
                if len(group_buffer_bytes) > self.MAX_CHUNK_SIZE:
                    remaining = iterator_node.end_byte - child.end_byte
                    if remaining > self.CHUNK_TOLERANCE: flush_group()

            cursor = child.end_byte

        flush_group()
        
        if cursor < iterator_node.end_byte:
            glue_buffer_bytes += content[cursor : iterator_node.end_byte]
            
        if glue_buffer_bytes.strip():
             start = glue_start_byte if glue_start_byte is not None else cursor
             self._create_chunk(
                glue_buffer_bytes, start, iterator_node.end_byte, content,
                file_path, file_id, current_active_parent,
                nodes, contents, relations, semantic_captures=semantic_captures
            )

    def _handle_large_node(self, node: Node, content: bytes, prefix: bytes, prefix_start: int, 
                           file_path: str, file_id: str, parent_chunk_id: Optional[str],
                           nodes: List, contents: Dict, relations: List,
                           semantic_captures: List[Dict] = None):
        target_node = node
        if node.type == 'decorated_definition':
            defn = node.child_by_field_name('definition')
            if defn: target_node = defn
        elif node.type == 'export_statement':
            d = node.child_by_field_name('declaration') or node.child_by_field_name('value')
            if d: target_node = d

        body_node = target_node.child_by_field_name("body") or target_node.child_by_field_name("block")
        
        if not body_node:
            self._create_hard_split(
                prefix + content[node.start_byte:node.end_byte], prefix_start, content,
                file_path, file_id, parent_chunk_id, nodes, contents, relations, semantic_captures
            )
            return

        header_node_text = content[node.start_byte : body_node.start_byte]
        full_header = prefix + header_node_text
        
        if len(full_header) > self.MAX_CHUNK_SIZE * 0.6:
            # Header Chunk
            header_id = self._create_chunk(
                full_header, prefix_start, body_node.start_byte, content,
                file_path, file_id, parent_chunk_id,
                nodes, contents, relations, semantic_captures=semantic_captures
            )
            self._process_scope(target_node, content, file_path, file_id, header_id, nodes, contents, relations, semantic_captures=semantic_captures)
        else:
            self._process_scope(
                target_node, content, file_path, file_id, parent_chunk_id, 
                nodes, contents, relations, 
                initial_glue=full_header, initial_glue_start=prefix_start,
                is_breakdown_mode=True,
                semantic_captures=semantic_captures
            )

    def _create_hard_split(self, text_bytes: bytes, start_offset: int, full_content: bytes,
                           fpath: str, fid: str, pid: str,
                           nodes: List, contents: Dict, relations: List, semantic_captures: List[Dict] = None):
        total = len(text_bytes); cursor = 0
        first_fragment_id = None
        while cursor < total:
            end = min(cursor + self.MAX_CHUNK_SIZE, total)
            if end < total:
                nl = text_bytes.rfind(b'\n', cursor, end)
                if nl > cursor + (self.MAX_CHUNK_SIZE // 2): end = nl + 1
            chunk = text_bytes[cursor:end]
            current_pid = pid if not first_fragment_id else first_fragment_id
            
            cid = self._create_chunk(
                chunk, start_offset + cursor, start_offset + end, full_content,
                fpath, fid, current_pid, nodes, contents, relations, semantic_captures=semantic_captures
            )
            if not first_fragment_id: first_fragment_id = cid
            cursor = end

    def _create_chunk(self, text_bytes: bytes, s_byte: int, e_byte: int, full_content: bytes,
                      fpath: str, fid: str, pid: Optional[str], 
                      nodes: List, contents: Dict, relations: List,
                      semantic_captures: List[Dict] = None) -> Optional[str]:
        
        text = text_bytes.decode('utf-8', 'ignore')
        if not text.strip(): return None
        
        h = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if h not in contents: contents[h] = ChunkContent(h, text)
        cid = str(uuid.uuid4())
        if s_byte < 0: s_byte = 0
        s_line = full_content[:s_byte].count(b'\n') + 1
        e_line = s_line + text.count('\n')
        
        # [NEW] Semantic Enrichment
        matches = []
        if semantic_captures:
            for cap in semantic_captures:
                cap_start, cap_end = cap['start'], cap['end']
                # Contenimento o Intersezione significativa
                # Il chunk è "dentro" il capture O il capture è "dentro" il chunk
                if (s_byte >= cap_start and e_byte <= cap_end) or \
                   (cap_start >= s_byte and cap_end <= e_byte):
                    matches.append(cap['metadata'])

        metadata = {}
        if matches: metadata["semantic_matches"] = matches

        # Nota: type è rimosso da ChunkNode
        nodes.append(ChunkNode(
            cid, fid, fpath, h, 
            s_line, e_line, [s_byte, e_byte],
            metadata=metadata
        ))
        
        if pid:
             relations.append(CodeRelation(
                source_file=fpath, target_file=fpath, relation_type="child_of",
                source_id=cid, target_id=pid, metadata={"tool": "treesitter_repo_parser"}
            ))
            
        return cid