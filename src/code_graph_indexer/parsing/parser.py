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
    5. Metadata Enrichment: Normalizza i tipi e estrae tag senza alterare lo splitting.
    """
    
    LANGUAGE_MAP = {
        ".py": "python", ".js": "javascript", ".jsx": "javascript", 
        ".ts": "typescript", ".tsx": "typescript", ".go": "go", 
        ".java": "java", ".c": "c", ".cpp": "cpp", ".rs": "rust",
        ".html": "html", ".css": "css", ".php": "php"
    }
    
    # Directory da ignorare sempre
    IGNORE_DIRS = {
        ".git", "node_modules", "target", "build", "dist", 
        ".venv", "venv", "env", ".env", "__pycache__", ".idea", ".vscode",
        "site-packages", "bin", "obj", "lib", "include", "eggs", ".eggs",
        "vendor", "bower_components", "jspm_packages"
    }

    # Pattern file da ignorare (suffissi)
    IGNORE_FILE_SUFFIXES = {
        ".min.js", ".min.css", ".bundle.js", ".bundle.css",
        ".map", ".test.js", ".spec.js", "-lock.json", ".lock"
    }

    # Limite dimensione chunk (~20-30 righe)
    MAX_CHUNK_SIZE = 800 
    
    # Tolleranza anti-widow
    CHUNK_TOLERANCE = 400

    CONTAINER_TYPES = {
        "class_definition", "class_declaration", "function_definition", "method_definition", 
        "function_declaration", "arrow_function", "interface_declaration", "impl_item", "mod_item",
        "async_function_definition", "decorated_definition", "export_statement"
    }

    GLUE_TYPES = {
        "comment", "decorator", "line_comment", "block_comment", "string_literal"
    }

    # Mappa di normalizzazione (Raw Type -> Macro Type per DB)
    NORMALIZED_TYPES = {
        # Classi
        "class_definition": "class_definition", "class_declaration": "class_definition",
        "struct_specifier": "class_definition", "impl_item": "class_definition",
        "interface_declaration": "interface_definition", "protocol_definition": "interface_definition",
        # Funzioni
        "function_definition": "function_definition", "function_declaration": "function_definition",
        "arrow_function": "function_definition", "async_function_definition": "function_definition",
        "generator_function": "function_definition", "generator_function_declaration": "function_definition",
        # Metodi
        "method_definition": "method_definition", "constructor": "method_definition",
        "method_declaration": "method_definition",
        # Moduli
        "module": "module_definition", "namespace_definition": "module_definition",
    }

    # Mappa: tipo semantico -> categoria macro per il DB / ranking
    CHUNK_KIND_MAP = {
        "class_definition": "class",
        "interface_definition": "class",
        "function_definition": "function",
        "method_definition": "method",
        "module_definition": "module",
        "comment_block": "comments",
        "code_fragment": "fragment",
        "code_block": "block",
    }

    def __init__(self, repo_path: str, metadata_provider: Optional[MetadataProvider] = None):
        self.repo_path = os.path.abspath(repo_path)
        if not os.path.isdir(self.repo_path): raise FileNotFoundError(f"Invalid path: {repo_path}")

        # [FIX] Usa os.path.exists invece di isdir per supportare Git Worktrees (dove .git è un file)
        is_git_repo = os.path.exists(os.path.join(self.repo_path, ".git"))
        
        self.metadata_provider = metadata_provider or (
            GitMetadataProvider(self.repo_path) if is_git_repo else LocalMetadataProvider(self.repo_path)
        )
        
        self.repo_info = self.metadata_provider.get_repo_info()
        self.repo_id = self.repo_info.get('repo_id', str(uuid.uuid4()))
        
        self.languages: Dict[str, Any] = {}
        lang_cache: Dict[str, Any] = {}
        
        # Costruiamo una mappa estensione -> Language, riusando le istanze per lo stesso linguaggio
        for ext, lang_name in self.LANGUAGE_MAP.items():
            if lang_name in lang_cache:
                # Riusa la stessa Language per tutte le estensioni che puntano allo stesso linguaggio
                self.languages[ext] = lang_cache[lang_name]
                continue
            try:
                lang_obj = get_language(lang_name)
            except Exception:
                # Se la grammatica non è disponibile (es. non compilata), salta TUTTE le estensioni che la usano
                continue
            lang_cache[lang_name] = lang_obj
            self.languages[ext] = lang_obj

        self.parser = Parser()

    def _safe_get_lang(self, lang):
        try: get_language(lang); return True
        except: return False

    def _set_parser_language(self, lang_object):
        if hasattr(self.parser, 'set_language'): self.parser.set_language(lang_object)
        else: self.parser.language = lang_object

    def _is_minified_or_generated(self, content: bytes, file_path: str) -> bool:
        """
        Rileva se un file è minificato o generato automaticamente.
        1. Check nome file (es. .min.js)
        2. Check euristico sul contenuto (linee lunghissime)
        """
        # 1. Check estensione/suffisso
        path_lower = file_path.lower()
        if any(path_lower.endswith(suffix) for suffix in self.IGNORE_FILE_SUFFIXES):
            return True
            
        # 2. Check euristico su contenuto (solo per file testuali)
        # Se troviamo righe > 2000 caratteri, è molto probabile sia minificato
        try:
            # Leggiamo solo i primi 4KB per performance
            sample = content[:4096]
            if b'\n' not in sample and len(sample) > 1000:
                # Singola riga lunga > 1000 char -> minificato
                return True
            
            # O se c'è una riga specifica molto lunga
            lines = sample.split(b'\n')
            for line in lines:
                if len(line) > 2500: # Soglia conservativa
                    return True
        except:
            pass
            
        return False

    def stream_semantic_chunks(self, file_list: Optional[List[str]] = None) -> Generator[Tuple[FileRecord, List[ChunkNode], List[ChunkContent], List[CodeRelation]], None, None]:
        files_to_process = set(file_list) if file_list else None
        
        for root, dirs, files in os.walk(self.repo_path, topdown=True):
            dirs[:] = [d for d in dirs if d.lower() not in self.IGNORE_DIRS]
            
            for file_name in files:
                _, ext = os.path.splitext(file_name)
                lang_object = self.languages.get(ext)
                if not lang_object:
                    # Nessuna grammatica disponibile per questa estensione, saltiamo il file
                    continue
                
                full_path = os.path.join(root, file_name)
                rel_path = os.path.relpath(full_path, self.repo_path)
                
                if files_to_process and rel_path not in files_to_process: continue

                try:
                    with open(full_path, 'rb') as f: content = f.read()
                    
                    # FILTER: Skip minified files
                    if self._is_minified_or_generated(content, rel_path):
                        print(f"[SKIP] Ignored minified/generated file: {rel_path}")
                        continue

                    file_rec = FileRecord(
                        id=str(uuid.uuid4()), repo_id=self.repo_id, commit_hash=self.repo_info.get('commit_hash', 'HEAD'),
                        file_hash=self.metadata_provider.get_file_hash(rel_path, content), path=rel_path,
                        language=self.LANGUAGE_MAP[ext], size_bytes=len(content),
                        category=self.metadata_provider.get_file_category(rel_path),
                        indexed_at=datetime.datetime.utcnow().isoformat() + "Z"
                    )

                    self._set_parser_language(lang_object)
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

    # --- HELPERS PER METADATA ---

    def _get_canonical_type(self, raw_type: str, parent_type: str = None) -> str:
        """Converte tipi specifici in macro-categorie per il RAG."""
        
        # Mappatura diretta se esiste
        if raw_type in self.NORMALIZED_TYPES:
            canonical = self.NORMALIZED_TYPES[raw_type]
            # Se è una funzione, controlla il contesto per promuoverla a metodo
            if canonical == "function_definition":
                # Se il padre è una classe, allora è un metodo
                if parent_type and self.NORMALIZED_TYPES.get(parent_type) == "class_definition":
                    return "method_definition"
            return canonical
            
        # Fallback per firme
        if raw_type.endswith("_signature") or raw_type.endswith("_header"):
             base = raw_type.replace("_signature", "").replace("_header", "")
             return self._get_canonical_type(base, parent_type)

        return "code_block"

    def _extract_tags(self, node: Node) -> List[str]:
        """
        Estrae tag semantici dal nodo e dai figli immediati.
        Non usa node.text (non disponibile nel binding Python),
        solo i tipi sintattici.
        """
        tags: List[str] = []

        # Async: function/method async o con keyword async tra i figli
        if node.type.startswith("async_") or any(c.type == "async" for c in node.children):
            tags.append("async")

        # Decorated: @decorator o wrapped in decorated_definition
        if node.type == "decorated_definition" or any(c.type == "decorator" for c in node.children):
            tags.append("decorated")

        # Export (TS/JS)
        if node.type == "export_statement" or (node.parent and node.parent.type == "export_statement"):
            tags.append("exported")

        # Constructor (TS/Java, ecc.)
        if "constructor" in node.type:
            tags.append("constructor")

        # Static methods / fields
        for child in node.children:
            if child.type == "static":
                tags.append("static")
            # Se in futuro vuoi vedere public/private/protected,
            # qui potrai usare content[start_byte:end_byte] con un helper.

        # Rimuovi duplicati
        return list(set(tags))

    def _determine_effective_type(self, node: Node) -> str:
        # 1. Identifica il tipo "interno" se wrappato
        effective_child_type = node.type
        if node.type == 'decorated_definition':
            defn = node.child_by_field_name('definition')
            if defn:
                effective_child_type = defn.type
        elif node.type == 'export_statement':
             decl = node.child_by_field_name('declaration') or node.child_by_field_name('value')
             if decl:
                 effective_child_type = decl.type

        # 2. Identifica il tipo del genitore semantico
        p_node = node.parent
        semantic_parent_type = None
        
        # Risaliamo la catena dei genitori ignorando i wrapper non semantici
        while p_node:
            # Se il padre è un blocco generico, continua a salire
            if p_node.type in ["block", "body", "declaration_list", "program", "module"]:
                p_node = p_node.parent
                continue
            
            # Se il padre è un wrapper (decorated/export), dobbiamo vedere cosa contiene
            check_type = p_node.type
            if check_type == 'decorated_definition':
                defn = p_node.child_by_field_name('definition')
                if defn: check_type = defn.type
            elif check_type == 'export_statement':
                decl = p_node.child_by_field_name('declaration') or p_node.child_by_field_name('value')
                if decl: check_type = decl.type
            
            # Trovato un genitore significativo?
            if check_type in self.NORMALIZED_TYPES:
                semantic_parent_type = check_type
                break
                
            # Altrimenti continua a salire (es. if_statement, try_statement)
            p_node = p_node.parent
        
        return self._get_canonical_type(effective_child_type, semantic_parent_type)

    # ==============================================================================
    #  LOGICA DI CHUNKING (Conservativa)
    # ==============================================================================

    def _process_scope(self, parent_node: Node, content: bytes, file_path: str, file_id: str, parent_chunk_id: Optional[str],
                       nodes: List, contents: Dict, relations: List, 
                       initial_glue: bytes = b"", initial_glue_start: Optional[int] = None,
                       is_breakdown_mode: bool = False,
                       active_override_type: Optional[str] = None):
        
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
                ctype = "code_block"
                if is_breakdown_mode and not first_chunk_created_in_scope and active_override_type:
                    ctype = active_override_type

                cid = self._create_chunk(
                    group_buffer_bytes, group_start_byte, group_end_byte, content,
                    ctype, file_path, file_id, current_active_parent,
                    nodes, contents, relations
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
                
                # Override inheritance logic
                override_to_pass = active_override_type if (is_breakdown_mode and not first_chunk_created_in_scope) else None

                if len(full_barrier_text) > self.MAX_CHUNK_SIZE:
                    self._handle_large_node(
                        child, content, glue_buffer_bytes, barrier_start, file_path, file_id, current_active_parent,
                        nodes, contents, relations,
                        inherited_override_type=override_to_pass
                    )
                    # Update state to prevent override from leaking to siblings
                    if is_breakdown_mode and not first_chunk_created_in_scope:
                         first_chunk_created_in_scope = True
                else:
                    # --- CALCOLO CANONICAL TYPE ---
                    canonical_type = self._determine_effective_type(child)
                    
                    # Apply override if applicable
                    if is_breakdown_mode and not first_chunk_created_in_scope and active_override_type:
                        canonical_type = active_override_type

                    cid = self._create_chunk(
                        full_barrier_text, barrier_start, barrier_end, content,
                        canonical_type, file_path, file_id, current_active_parent,
                        nodes, contents, relations, tags=self._extract_tags(child)
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
                "comment_block", file_path, file_id, current_active_parent,
                nodes, contents, relations, tags=[]
            )

    def _handle_large_node(self, node: Node, content: bytes, prefix: bytes, prefix_start: int, 
                           file_path: str, file_id: str, parent_chunk_id: Optional[str],
                           nodes: List, contents: Dict, relations: List,
                           inherited_override_type: Optional[str] = None):
        
        target_node = node
        if node.type == 'decorated_definition':
            definition = node.child_by_field_name('definition')
            if definition: target_node = definition
        elif node.type == 'export_statement':
            d = node.child_by_field_name('declaration') or node.child_by_field_name('value')
            if d: target_node = d

        body_node = target_node.child_by_field_name("body") or target_node.child_by_field_name("block")
        
        if not body_node:
            self._create_hard_split(
                prefix + content[node.start_byte:node.end_byte], prefix_start, content,
                file_path, file_id, parent_chunk_id, nodes, contents, relations
            )
            return

        header_node_text = content[node.start_byte : body_node.start_byte]
        full_header = prefix + header_node_text
        
        HEADER_SPLIT_THRESHOLD = self.MAX_CHUNK_SIZE * 0.6 
        is_header_huge = (len(full_header) > HEADER_SPLIT_THRESHOLD)
        
        if is_header_huge:
            # Header Separato -> Diventa Parent Esplicito
            # Calculate type for the header chunk
            my_type = self._determine_effective_type(node)
            
            header_type = inherited_override_type if inherited_override_type else f"{my_type}_signature"

            header_id = self._create_chunk(
                full_header, prefix_start, body_node.start_byte, content,
                header_type, file_path, file_id, parent_chunk_id,
                nodes, contents, relations, tags=self._extract_tags(node)
            )
            self._process_scope(target_node, content, file_path, file_id, header_id, nodes, contents, relations)
        else:
            # Flow-Down -> Primo figlio diventa Parent (Surrogate)
            # Determine what override to pass down
            my_type = self._determine_effective_type(node)
            next_override = inherited_override_type if inherited_override_type else my_type

            self._process_scope(
                target_node, content, file_path, file_id, parent_chunk_id, 
                nodes, contents, relations, 
                initial_glue=full_header, initial_glue_start=prefix_start,
                is_breakdown_mode=True,
                active_override_type=next_override
            )

    def _create_hard_split(self, text_bytes: bytes, start_offset: int, full_content: bytes,
                           fpath: str, fid: str, pid: str,
                           nodes: List, contents: Dict, relations: List):
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
                "code_fragment", fpath, fid, current_pid, nodes, contents, relations, tags=[]
            )
            if not first_fragment_id: first_fragment_id = cid
            cursor = end

    def _create_chunk(self, text_bytes: bytes, s_byte: int, e_byte: int, full_content: bytes,
                      ctype: str, fpath: str, fid: str, pid: Optional[str], 
                      nodes: List, contents: Dict, relations: List,
                      tags: List[str] = None) -> Optional[str]:
        """
        Crea un ChunkNode + ChunkContent.
        
        - ctype: tipo semantico specifico (es. 'function_definition', 'class_definition', 'code_block')
        - ChunkNode.type: categoria macro ('function', 'class', 'method', 'block', ecc.)
        - metadata.original_type: mantiene il tipo originale specifico.
        - metadata.tags: async/static/exported... se presenti.
        """
        text = text_bytes.decode('utf-8', 'ignore')
        if not text.strip(): return None
        
        h = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if h not in contents: contents[h] = ChunkContent(h, text)
        cid = str(uuid.uuid4())
        if s_byte < 0: s_byte = 0
        s_line = full_content[:s_byte].count(b'\n') + 1
        e_line = s_line + text.count('\n')
        
        # Canonicalizziamo il tipo in una categoria macro leggibile
        # Normalizza tipi signature/header prima di mappare il kind
        base_ctype = ctype
        if ctype.endswith("_signature") or ctype.endswith("_header"):
            base_ctype = ctype.replace("_signature", "").replace("_header", "")
        
        kind = self.CHUNK_KIND_MAP.get(base_ctype, "block")

        metadata = {"original_type": ctype}
        if tags: metadata["tags"] = tags

        nodes.append(ChunkNode(
            cid, fid, fpath, h, kind,
            s_line, e_line, [s_byte, e_byte],
            metadata=metadata
        ))
        
        if pid:
             relations.append(CodeRelation(
                source_file=fpath, target_file=fpath, relation_type="child_of",
                source_id=cid, target_id=pid, metadata={"tool": "treesitter_repo_parser"}
            ))
            
        return cid