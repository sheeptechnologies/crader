import os
import logging
import concurrent.futures
import multiprocessing
import itertools
import json
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

# Componenti Interni
from .volume_manager.git_volume_manager import GitVolumeManager
from .parsing.parser import TreeSitterRepoParser
from .graph.indexers.scip import SCIPIndexer, SCIPRunner
from .graph.builder import KnowledgeGraphBuilder
from .storage.postgres import PostgresGraphStorage
from .storage.connector import PooledConnector, SingleConnector
from .embedding.embedder import CodeEmbedder
from .providers.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)

# ==============================================================================
#  WORKER FUNCTIONS (ISOLATED CONTEXT)
# ==============================================================================

_worker_parser = None
_worker_storage = None

def _init_worker_process(worktree_path: str, snapshot_id: str, commit_hash: str, repo_url: str, branch: str, db_url: str):
    """
    Bootstrap del Worker Process.
    Ogni worker si crea il proprio SingleConnector dedicato verso PgBouncer.
    """
    global _worker_parser, _worker_storage
    
    # 1. Init Parser (Cpu Bound)
    # Import locale per evitare circular imports nel parent process se necessario
    from .parsing.parser import TreeSitterRepoParser
    _worker_parser = TreeSitterRepoParser(repo_path=worktree_path)
    _worker_parser.snapshot_id = snapshot_id
    _worker_parser.repo_info = {
        'commit_hash': commit_hash, 
        'url': repo_url, 
        'branch': branch, 
        'name': repo_url.split('/')[-1]
    }

    # 2. Init Storage (I/O Bound - Direct Connection)
    # Il worker usa una connessione dedicata "usa e getta"
    from .storage.postgres import PostgresGraphStorage
    from .storage.connector import SingleConnector
    
    try:
        # Creiamo il connettore dedicato (Zero Pool Overhead)
        connector = SingleConnector(dsn=db_url)
        _worker_storage = PostgresGraphStorage(connector=connector)
    except Exception as e:
        print(f"❌ [WORKER INIT ERROR] DB Connect failed: {e}")
        _worker_storage = None

def _process_and_insert_chunk(file_paths: List[str]) -> int:
    """
    Il worker parsifica un chunk di file e scrive direttamente su DB via COPY.
    Nessun dato viene ritornato al processo padre (Zero IPC overhead).
    """
    global _worker_parser, _worker_storage
    
    if not _worker_storage:
        return 0 # Fail safe

    # Buffer Locali (Tuple per COPY/INSERT raw)
    t_files = []
    t_nodes = []
    t_contents = []
    t_rels = []
    
    count = 0

    for f_path in file_paths:
        try:
            # Parsing stream
            for f_rec, nodes, contents, rels in _worker_parser.stream_semantic_chunks(file_list=[f_path]):
                
                # 1. File Tuple
                t_files.append((
                    f_rec.id, f_rec.snapshot_id, f_rec.commit_hash, f_rec.file_hash, f_rec.path, 
                    f_rec.language, f_rec.size_bytes, f_rec.category, f_rec.indexed_at, 
                    f_rec.parsing_status, f_rec.parsing_error
                ))
                
                # 2. Node Tuple (per COPY)
                for n in nodes:
                    bs, be = n.byte_range
                    t_nodes.append((
                        n.id, n.file_id, n.file_path, 
                        n.start_line, n.end_line, bs, be, 
                        n.chunk_hash, be - bs, 
                        json.dumps(n.metadata) 
                    ))
                
                # 3. Content Tuple
                for c in contents:
                    t_contents.append((c.chunk_hash, c.content))
                    
                # 4. Relation Tuple
                for r in rels:
                    t_rels.append((
                        r.source_id, r.target_id, r.relation_type, 
                        json.dumps(r.metadata)
                    ))
                
                count += 1
                
        except Exception as e:
            # Logghiamo l'errore ma continuiamo col prossimo file nel chunk
            print(f"⚠️ [WORKER ERROR] Processing {f_path}: {e}")
            continue

    # SCRITTURA DIRETTA SU DB
    # _worker_storage usa il suo SingleConnector interno -> Socket diretto a PgBouncer
    try:
        if t_files: _worker_storage.add_files_raw(t_files)
        if t_contents: _worker_storage.add_contents_raw(t_contents)
        if t_nodes: _worker_storage.add_nodes_raw(t_nodes) # COPY Command (Velocissimo)
        if t_rels: _worker_storage.add_relations_raw(t_rels)
    except Exception as e:
        print(f"❌ [WORKER DB ERROR] Write failed on chunk: {e}")
        return 0

    return count

def _chunked_iterable(iterable, size):
    it = iter(iterable)
    while True:
        chunk = tuple(itertools.islice(it, size))
        if not chunk: break
        yield chunk

# ==============================================================================
#  MAIN CLASS (ORCHESTRATOR)
# ==============================================================================

class CodebaseIndexer:
    def __init__(self, repo_url: str, branch: str, db_url: Optional[str] = None):
        """
        Inizializza l'indexer.
        
        Args:
            repo_url: URL della repository Git.
            branch: Branch da indicizzare.
            db_url: (Opzionale) Connection string Postgres. 
                    Se None, la cerca in os.environ["DB_URL"].
        """
        self.repo_url = repo_url
        self.branch = branch
        
        # Risoluzione DB URL
        self.db_url = db_url or os.getenv("DB_URL")
        if not self.db_url:
            raise ValueError("DB_URL non fornito e non trovato nelle variabili d'ambiente.")

        # Auto-Configurazione Storage (Main Process uses Pooled Connector)
        logger.info(f"🔌 Connecting to DB (Pool): {self.db_url.split('@')[-1]}")
        self.connector = PooledConnector(dsn=self.db_url)
        self.storage = PostgresGraphStorage(connector=self.connector)
        
        # Componenti interni
        self.git_manager = GitVolumeManager()
        self.builder = KnowledgeGraphBuilder(self.storage)

    def index(self, force: bool = False) -> str:
        """
        Esegue l'indicizzazione completa.
        """
        logger.info(f"🚀 Indexing Request Start: {self.repo_url} ({self.branch})")
        
        # 1. Identity Resolution
        repo_name = self.repo_url.rstrip('/').split('/')[-1].replace('.git', '')
        repo_id = self.storage.ensure_repository(self.repo_url, self.branch, repo_name)
        
        active_snapshot_id = None

        # === GREEDY WORKER LOOP ===
        while True:
            # Fase A: Network Sync
            logger.info("🌍 Syncing repository cache...")
            self.git_manager.ensure_repo_updated(self.repo_url)
            commit = self.git_manager.get_head_commit(self.repo_url, self.branch)
            
            # Fase B: Lock & Concurrency Check
            snapshot_id, is_new = self.storage.create_snapshot(repo_id, commit, force_new=force)
            
            if not is_new and snapshot_id is None:
                logger.info("⏸️  Repo occupata, richiesta accodata.")
                return "queued"
            
            if not is_new and snapshot_id and not force:
                logger.info(f"✅ Snapshot {snapshot_id} già valido.")
                return snapshot_id

            active_snapshot_id = snapshot_id
            
            try:
                # Fase C: Esecuzione in Ambiente Effimero
                with self.git_manager.ephemeral_worktree(self.repo_url, commit) as worktree_path:
                    logger.info(f"⚙️  Worktree montato in: {worktree_path}")
                    
                    self._run_indexing_pipeline(
                        repo_id=repo_id,
                        snapshot_id=snapshot_id,
                        commit=commit,
                        worktree_path=worktree_path
                    )

                # Fase D: Check Tail (Debounce)
                if self.storage.check_and_reset_reindex_flag(repo_id):
                    logger.info("🔁 Rilevata nuova richiesta pendente. Riavvio loop...")
                    force = True
                    continue 
                else:
                    logger.info("✅ Indicizzazione completata.")
                    break

            except Exception as e:
                logger.error(f"❌ Indexing Failed on {snapshot_id}: {e}", exc_info=True)
                self.storage.fail_snapshot(snapshot_id, str(e))
                raise e
        
        return active_snapshot_id

    def _run_indexing_pipeline(self, repo_id: str, snapshot_id: str, commit: str, worktree_path: str):
        """
        Coordina il lavoro parallelo.
        """
        scip_runner = SCIPRunner(repo_path=worktree_path)
        scip_indexer = SCIPIndexer(repo_path=worktree_path)
        previous_live_snapshot = self.storage.get_active_snapshot_id(repo_id)

        # File Discovery
        logger.info("🔍 Scanning files...")
        all_files = []
        IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", "target", "vendor"}
        
        for root, dirs, files in os.walk(worktree_path):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for file in files:
                _, ext = os.path.splitext(file)
                # Filtro base estensioni
                if ext in {'.py', '.js', '.ts', '.tsx', '.jsx', '.java', '.go', '.rs', '.c', '.cpp', '.php', '.html', '.css'}: 
                    rel_path = os.path.relpath(os.path.join(root, file), worktree_path)
                    all_files.append(rel_path)

        # Configurazione Parallelismo
        num_workers = max(1, multiprocessing.cpu_count() - 1)
        TASK_CHUNK_SIZE = 50 
        file_chunks = list(_chunked_iterable(all_files, TASK_CHUNK_SIZE))
        
        logger.info(f"🔨 Parsing & Writing with {num_workers} workers (Direct DB Mode)...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as scip_executor:
            # SCIP in background
            future_scip = scip_executor.submit(scip_runner.run_to_disk)

            # Workers in parallelo
            with concurrent.futures.ProcessPoolExecutor(
                max_workers=num_workers, 
                initializer=_init_worker_process,
                # Passiamo self.db_url esplicito ai worker
                initargs=(worktree_path, snapshot_id, commit, self.repo_url, self.branch, self.db_url)
            ) as executor:
                
                future_to_chunk = {
                    executor.submit(_process_and_insert_chunk, chunk): chunk 
                    for chunk in file_chunks
                }
                
                total_processed = 0
                completed_chunks = 0
                
                for future in concurrent.futures.as_completed(future_to_chunk):
                    try:
                        count = future.result()
                        total_processed += count
                        completed_chunks += 1
                        
                        if completed_chunks % 10 == 0:
                            logger.info(f"⏳ Processed {total_processed}/{len(all_files)} files...")
                            
                    except Exception as e:
                        logger.error(f"❌ Worker Error: {e}")

            # --- Integrazione SCIP (Main Thread) ---
            scip_json_path = future_scip.result()
            if scip_json_path and os.path.exists(scip_json_path):
                logger.info("🔗 Ingesting SCIP relations...")
                rel_gen = scip_indexer.stream_relations_from_file(scip_json_path)
                batch = []
                for rel in rel_gen:
                    batch.append(rel)
                    if len(batch) >= 5000:
                        self.builder.add_relations(batch, snapshot_id=snapshot_id)
                        batch = []
                if batch: 
                    self.builder.add_relations(batch, snapshot_id=snapshot_id)
                try:
                    os.remove(scip_json_path)
                except OSError: pass

        # Attivazione Finale
        # Nota: per le stats usiamo il main storage che è pooled e thread-safe
        current_stats = self.storage.get_stats()
        stats = {
            "files": total_processed, 
            "nodes": current_stats.get("total_nodes", 0),
            "engine": "v8_auto_config"
        }
        
        # Manifest (Placeholder per ora, o ricostruito se necessario)
        manifest_tree = {"type": "dir", "children": {}} 
        
        self.storage.activate_snapshot(repo_id, snapshot_id, stats, manifest=manifest_tree)
        logger.info(f"🚀 SNAPSHOT ACTIVATED: {snapshot_id}")

        if previous_live_snapshot and previous_live_snapshot != snapshot_id:
            logger.info(f"🧹 Pruning old snapshot {previous_live_snapshot}...")
            self.storage.prune_snapshot(previous_live_snapshot)

    def embed(self, provider: EmbeddingProvider, batch_size: int = 32, debug: bool = False, force_snapshot_id: str = None):
        """
        Genera embeddings. Usa lo storage interno (Pooled).
        """
        logger.info(f"🤖 Embedding Start: {provider.model_name}")
        repo_name = self.repo_url.rstrip('/').split('/')[-1].replace('.git', '')
        repo_id = self.storage.ensure_repository(self.repo_url, self.branch, repo_name)
        
        target_snapshot_id = force_snapshot_id or self.storage.get_active_snapshot_id(repo_id)
        if not target_snapshot_id:
             raise ValueError("No active snapshot found. Run index() first.")
        
        embedder = CodeEmbedder(self.storage, provider)
        yield from embedder.run_indexing(
            snapshot_id=target_snapshot_id,
            batch_size=batch_size, 
            yield_debug_docs=debug
        )

    def get_stats(self):
        return self.storage.get_stats()
    
    def close(self):
        """Chiude le connessioni del pool principale."""
        if hasattr(self, 'storage'):
            self.storage.close()