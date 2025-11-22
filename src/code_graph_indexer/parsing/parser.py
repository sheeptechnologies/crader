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
    """
    Parser semantico per RAG Enterprise.
    
    Logica Core:
    1. Glue Buffer: Accumula commenti e spazi. Non viene mai salvato da solo.
    2. Group Buffer: Raggruppa istruzioni piccole (import, var) per evitare micro-chunk.
    3. Barriers: Classi e Funzioni interrompono i gruppi e assorbono il Glue Buffer.
    4. Header Flow-Down: Le firme vengono unite al corpo (docstring) per preservare il contesto.
    5. Surrogate Parenting: In caso di split, il primo chunk diventa il padre logico dei successivi.
    """
    
    LANGUAGE_MAP = {
        ".py": "python", ".js": "javascript", ".jsx": "javascript", 
        ".ts": "typescript", ".tsx": "typescript", ".go": "go", 
        ".java": "java", ".c": "c", ".cpp": "cpp", ".rs": "rust",
        ".html": "html", ".css": "css", ".php": "php"
    }
    
    IGNORE_DIRS = {
        ".git", "node_modules", "target", "build", "dist", 
        ".venv", "venv", "env", ".env", "__pycache__", ".idea", ".vscode",
        "site-packages", "bin", "obj", "lib", "include", "eggs", ".eggs"
    }

    # Limite dimensione chunk (~20-30 righe)
    MAX_CHUNK_SIZE = 800 
    
    # Tolleranza anti-widow (evita di tagliare ultimi statement se siamo vicini alla fine)
    CHUNK_TOLERANCE = 400

    CONTAINER_TYPES = {
        "class_definition", "class_declaration", "function_definition", "method_definition", 
        "function_declaration", "arrow_function", "interface_declaration", "impl_item", "mod_item",
        "async_function_definition", "decorated_definition"
    }

    GLUE_TYPES = {
        "comment", "decorator", "line_comment", "block_comment", "string_literal"
    }

    def __init__(self, repo_path: str, metadata_provider: Optional[MetadataProvider] = None):
        self.repo_path = os.path.abspath(repo_path)
        if not os.path.isdir(self.repo_path): raise FileNotFoundError(f"Invalid path: {repo_path}")
        
        self.metadata_provider = metadata_provider or (GitMetadataProvider(self.repo_path) if os.path.isdir(os.path.join(self.repo_path, ".git")) else LocalMetadataProvider(self.repo_path))
        self.repo_info = self.metadata_provider.get_repo_info()
        self.repo_id = self.repo_info.get('repo_id', str(uuid.uuid4()))
        
        self.languages = {ext: get_language(lang) for ext, lang in self.LANGUAGE_MAP.items() if self._safe_get_lang(lang)}
        self.parser = Parser()

    def _safe_get_lang(self, lang):
        try: get_language(lang); return True
        except: return False

    def _set_parser_language(self, lang_object):
        if hasattr(self.parser, 'set_language'): self.parser.set_language(lang_object)
        else: self.parser.language = lang_object

    def stream_semantic_chunks(self, file_list: Optional[List[str]] = None) -> Generator[Tuple[FileRecord, List[ChunkNode], List[ChunkContent], List[CodeRelation]], None, None]:
        files_to_process = set(file_list) if file_list else None
        
        for root, dirs, files in os.walk(self.repo_path, topdown=True):
            dirs[:] = [d for d in dirs if d.lower() not in self.IGNORE_DIRS]
            
            for file_name in files:
                _, ext = os.path.splitext(file_name)
                if ext not in self.LANGUAGE_MAP: continue
                
                full_path = os.path.join(root, file_name)
                rel_path = os.path.relpath(full_path, self.repo_path)
                
                if files_to_process and rel_path not in files_to_process: continue

                try:
                    with open(full_path, 'rb') as f: content = f.read()
                    
                    file_rec = FileRecord(
                        id=str(uuid.uuid4()), repo_id=self.repo_id, commit_hash=self.repo_info.get('commit_hash', 'HEAD'),
                        file_hash=self.metadata_provider.get_file_hash(rel_path, content), path=rel_path,
                        language=self.LANGUAGE_MAP[ext], size_bytes=len(content),
                        category=self.metadata_provider.get_file_category(rel_path),
                        indexed_at=datetime.datetime.utcnow().isoformat() + "Z"
                    )

                    self._set_parser_language(self.languages[ext])
                    tree = self.parser.parse(content)
                    
                    nodes = []; contents = {}; relations = []
                    
                    self._process_scope(tree.root_node, content, rel_path, file_rec.id, None, nodes, contents, relations)

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

    # ==============================================================================
    #  LOGICA DI CHUNKING
    # ==============================================================================

    def _process_scope(self, parent_node: Node, content: bytes, file_path: str, file_id: str, parent_chunk_id: Optional[str],
                       nodes: List, contents: Dict, relations: List, 
                       initial_glue: bytes = b"", initial_glue_start: Optional[int] = None,
                       is_breakdown_mode: bool = False):
        
        body_node = parent_node.child_by_field_name("body") or parent_node.child_by_field_name("block") or parent_node.child_by_field_name("consequence")
        iterator_node = body_node if body_node else parent_node
        
        cursor = iterator_node.start_byte
        
        # Buffer 1: Glue (Commenti/Spazi)
        glue_buffer_bytes = initial_glue 
        glue_start_byte = initial_glue_start 
        if initial_glue and glue_start_byte is None:
            glue_start_byte = iterator_node.start_byte 

        # Buffer 2: Group (Istruzioni piccole)
        group_buffer_bytes = b""
        group_start_byte = None
        group_end_byte = None

        # Stato per Surrogate Parenting (se in breakdown)
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
                    "code_block", file_path, file_id, current_active_parent,
                    nodes, contents, relations
                )
                register_chunk_creation(cid)
                
                group_buffer_bytes = b""
                group_start_byte = None
                group_end_byte = None

        for child in iterator_node.children:
            # 1. CATTURA GAP
            if child.start_byte > cursor:
                gap = content[cursor : child.start_byte]
                glue_buffer_bytes += gap
                if glue_start_byte is None: glue_start_byte = cursor

            # 2. CLASSIFICAZIONE
            child_type = child.type
            is_glue = (
                child_type in self.GLUE_TYPES or 
                child_type.startswith('comment') or
                (child_type == 'expression_statement' and child.child_count > 0 and child.children[0].type == 'string')
            )
            
            is_barrier = (child_type in self.CONTAINER_TYPES)
            
            child_bytes = content[child.start_byte : child.end_byte]
            
            if is_glue:
                # === GLUE ===
                glue_buffer_bytes += child_bytes
                if glue_start_byte is None: glue_start_byte = child.start_byte
            
            elif is_barrier:
                # === BARRIERA ===
                flush_group()
                
                full_barrier_text = glue_buffer_bytes + child_bytes
                barrier_start = glue_start_byte if glue_start_byte is not None else child.start_byte
                barrier_end = child.end_byte
                
                if len(full_barrier_text) > self.MAX_CHUNK_SIZE:
                    self._handle_large_node(
                        child, content, glue_buffer_bytes, barrier_start, file_path, file_id, current_active_parent,
                        nodes, contents, relations
                    )
                    # Nota: _handle_large_node gestisce internamente i figli, 
                    # ma non aggiorna first_chunk_created_in_scope qui perché è un ramo parallelo.
                else:
                    cid = self._create_chunk(
                        full_barrier_text, barrier_start, barrier_end, content,
                        child_type, file_path, file_id, current_active_parent,
                        nodes, contents, relations
                    )
                    register_chunk_creation(cid)
                
                glue_buffer_bytes = b""
                glue_start_byte = None

            else:
                # === ISTRUZIONE PICCOLA ===
                if group_buffer_bytes == b"":
                    group_start_byte = glue_start_byte if glue_start_byte is not None else child.start_byte
                
                group_buffer_bytes += glue_buffer_bytes
                glue_buffer_bytes = b""
                glue_start_byte = None
                
                group_buffer_bytes += child_bytes
                group_end_byte = child.end_byte
                
                # Controllo dimensione + Anti-Widow
                if len(group_buffer_bytes) > self.MAX_CHUNK_SIZE:
                    remaining = iterator_node.end_byte - child.end_byte
                    if remaining > self.CHUNK_TOLERANCE:
                        flush_group()

            cursor = child.end_byte

        # FINAL FLUSH
        flush_group()
        
        if cursor < iterator_node.end_byte:
            glue_buffer_bytes += content[cursor : iterator_node.end_byte]
            
        if glue_buffer_bytes.strip():
             start = glue_start_byte if glue_start_byte is not None else cursor
             self._create_chunk(
                glue_buffer_bytes, start, iterator_node.end_byte, content,
                "comment_block", file_path, file_id, current_active_parent,
                nodes, contents, relations
            )

    def _handle_large_node(self, node: Node, content: bytes, prefix: bytes, prefix_start: int, 
                           file_path: str, file_id: str, parent_chunk_id: Optional[str],
                           nodes: List, contents: Dict, relations: List):
        
        target_node = node
        if node.type == 'decorated_definition':
            definition = node.child_by_field_name('definition')
            if definition: target_node = definition
        
        body_node = target_node.child_by_field_name("body") or target_node.child_by_field_name("block")
        
        if not body_node:
            self._create_hard_split(
                prefix + content[node.start_byte:node.end_byte], prefix_start, content,
                file_path, file_id, parent_chunk_id, nodes, contents, relations
            )
            return

        header_node_text = content[node.start_byte : body_node.start_byte]
        full_header = prefix + header_node_text
        header_start = node.start_byte - len(prefix)
        
        HEADER_SPLIT_THRESHOLD = self.MAX_CHUNK_SIZE * 0.6 
        is_header_huge = (len(full_header) > HEADER_SPLIT_THRESHOLD)
        
        if is_header_huge:
            # Header Separato -> Diventa Parent Esplicito
            header_id = self._create_chunk(
                full_header, prefix_start, body_node.start_byte, content,
                f"{node.type}_signature", file_path, file_id, parent_chunk_id,
                nodes, contents, relations
            )
            self._process_scope(target_node, content, file_path, file_id, header_id, nodes, contents, relations)
        else:
            # Flow-Down -> Primo figlio diventa Parent (Surrogate)
            self._process_scope(
                target_node, content, file_path, file_id, parent_chunk_id, 
                nodes, contents, relations, 
                initial_glue=full_header, initial_glue_start=prefix_start,
                is_breakdown_mode=True 
            )

    def _create_hard_split(self, text_bytes: bytes, start_offset: int, full_content: bytes,
                           fpath: str, fid: str, pid: str,
                           nodes: List, contents: Dict, relations: List):
        total = len(text_bytes)
        cursor = 0
        first_fragment_id = None
        
        while cursor < total:
            end = min(cursor + self.MAX_CHUNK_SIZE, total)
            if end < total:
                nl = text_bytes.rfind(b'\n', cursor, end)
                if nl > cursor + (self.MAX_CHUNK_SIZE // 2):
                    end = nl + 1
            
            chunk = text_bytes[cursor:end]
            current_pid = pid
            if first_fragment_id and pid:
                current_pid = first_fragment_id

            cid = self._create_chunk(
                chunk, start_offset + cursor, start_offset + end, full_content,
                "code_fragment", fpath, fid, current_pid, nodes, contents, relations
            )
            
            if first_fragment_id is None:
                first_fragment_id = cid
                
            cursor = end

    def _create_chunk(self, text_bytes: bytes, s_byte: int, e_byte: int, full_content: bytes,
                      ctype: str, fpath: str, fid: str, pid: Optional[str], 
                      nodes: List, contents: Dict, relations: List) -> str:
        
        text = text_bytes.decode('utf-8', 'ignore')
        if not text.strip(): return None
        
        h = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if h not in contents: contents[h] = ChunkContent(h, text)
        
        cid = str(uuid.uuid4())
        
        if s_byte < 0: s_byte = 0
        s_line = full_content[:s_byte].count(b'\n') + 1
        e_line = s_line + text.count('\n')
        
        nodes.append(ChunkNode(
            cid, fid, fpath, h, ctype,
            s_line, e_line, [s_byte, e_byte]
        ))
        
        if pid:
             relations.append(CodeRelation(
                source_file=fpath, target_file=fpath, relation_type="child_of",
                source_id=cid, target_id=pid, metadata={"tool": "treesitter_repo_parser"}
            ))
            
        return cid