import os

os.environ["GRPC_ENABLE_FORK_SUPPORT"] = "0" 
os.environ["GRPC_POLL_STRATEGY"] = "poll"
import gc

import logging
import concurrent.futures
import multiprocessing
import itertools
from contextlib import ExitStack
import json
from typing import Callable, Dict, Any, List, Optional, Tuple

from opentelemetry import trace, context
from opentelemetry.propagate import inject, extract

tracer = trace.get_tracer(__name__)

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

def _init_worker_process(worktree_path: str, snapshot_id: str, commit_hash: str, repo_url: str, branch: str, db_url: str,worker_init_fn: Optional[Callable]):
    """
    Bootstrap del Worker Process.
    Ogni worker si crea il proprio SingleConnector dedicato verso PgBouncer.
    """
    if worker_init_fn:
        try:
            worker_init_fn()
        except Exception as e:
            print(f"⚠️ [WORKER INIT] Custom telemetry setup failed: {e}")

    global _worker_parser, _worker_storage
    
    # 1. Init Parser (Cpu Bound)
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
    # Il worker usa una connessione dedicata "usa e getta" per massimizzare la velocità di COPY
    from .storage.postgres import PostgresGraphStorage
    from .storage.connector import SingleConnector
    
    try:
        # Creiamo il connettore dedicato (Zero Pool Overhead)
        connector = SingleConnector(dsn=db_url)
        _worker_storage = PostgresGraphStorage(connector=connector)
    except Exception as e:
        print(f"❌ [WORKER INIT ERROR] DB Connect failed: {e}")
        _worker_storage = None


def _process_and_insert_chunk(file_paths: List[str], carrier: Dict[str, str]) -> Tuple[int, Dict[str, float]]:
    """
    Worker ottimizzato con Micro-Batching per evitare OOM su chunk grandi.
    """
    gc.disable()
    global _worker_parser, _worker_storage
    if not _worker_storage: return 0, {}

    ctx = extract(carrier)
    
    # Parametri di Tuning Memoria
    BATCH_SIZE_NODES = 50000  # Flush ogni 50k nodi (max efficienza COPY)
    BATCH_SIZE_FILES = 500     # Flush ogni 500 file
    
    # Buffer Locali
    buffer = {
        'files': [], 'nodes': [], 'contents': [], 'rels': []
    }
    
    processed_count = 0

    def flush_buffers():
        """Helper interno per scaricare i buffer su DB."""
        if not (buffer['files'] or buffer['nodes']): return
        
        try:
            with tracer.start_as_current_span("worker.db_flush") as db_span:
                db_span.set_attribute("nodes.count", len(buffer['nodes']))
                db_span.set_attribute("files.count", len(buffer['files']))
                
                if buffer['files']: _worker_storage.add_files_raw(buffer['files'])
                if buffer['contents']: _worker_storage.add_contents_raw(buffer['contents'])
                if buffer['nodes']: _worker_storage.add_nodes_raw(buffer['nodes'])
                if buffer['rels']: _worker_storage.add_relations_raw(buffer['rels'])
                
            # Reset buffer (Liberiamo RAM!)
            buffer['files'].clear()
            buffer['nodes'].clear()
            buffer['contents'].clear()
            buffer['rels'].clear()
            
        except Exception as e:
            logger.error(f"❌ [WORKER FLUSH ERROR] {e}")
            raise e

    with tracer.start_as_current_span("worker.process_chunk", context=ctx) as span:
        span.set_attribute("chunk.total_files", len(file_paths))
        span.set_attribute("process.pid", os.getpid())

        for f_path in file_paths:
            try:
                # Parsing Stream
                for f_rec, nodes, contents, rels in _worker_parser.stream_semantic_chunks(file_list=[f_path]):
                    # Accumulo Files
                    buffer['files'].append((
                        f_rec.id, f_rec.snapshot_id, f_rec.commit_hash, f_rec.file_hash, f_rec.path, 
                        f_rec.language, f_rec.size_bytes, f_rec.category, f_rec.indexed_at, 
                        f_rec.parsing_status, f_rec.parsing_error
                    ))
                    
                    # Accumulo Nodes
                    for n in nodes:
                        bs, be = n.byte_range
                        buffer['nodes'].append((
                            n.id, n.file_id, n.file_path, n.start_line, n.end_line, bs, be, 
                            n.chunk_hash, be - bs, json.dumps(n.metadata) 
                        ))
                    
                    # Accumulo Contents & Rels
                    for c in contents: buffer['contents'].append((c.chunk_hash, c.content))
                    for r in rels: buffer['rels'].append((r.source_id, r.target_id, r.relation_type, json.dumps(r.metadata)))
                    
                    processed_count += 1

                    # === SMART FLUSH CHECK ===
                    # Se abbiamo troppi dati in RAM, scarichiamo subito
                    if len(buffer['nodes']) >= BATCH_SIZE_NODES or len(buffer['files']) >= BATCH_SIZE_FILES:
                        with tracer.start_as_current_span("worker.flush_buffers", context=ctx):
                            flush_buffers()
                
            except Exception as e:
                span.record_exception(e)
                logger.warning(f"⚠️ Skipping {f_path}: {e}")
                continue

        # Flush finale per i residui
        flush_buffers()
        
        return processed_count, {}

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
    def __init__(self, repo_url: str, branch: str, db_url: Optional[str] = None, worker_telemetry_init: Optional[Callable[[], None]] = None):
        """
        Inizializza l'indexer.
        Si auto-configura usando db_url o la variabile d'ambiente DB_URL.
        """
        self.repo_url = repo_url
        self.branch = branch
        self.worker_telemetry_init = worker_telemetry_init
        
        # Risoluzione DB URL
        self.db_url = db_url or os.getenv("DB_URL")
        if not self.db_url:
            raise ValueError("DB_URL non fornito e non trovato nelle variabili d'ambiente.")

        # Auto-Configurazione Storage (Main Process uses Pooled Connector)
        # Il Main Thread usa un Pool per gestire le operazioni di coordinamento in modo thread-safe
        safe_log_url = self.db_url.split('@')[-1] if '@' in self.db_url else "..."
        logger.info(f"🔌 Connecting to DB (Pool): {safe_log_url}")
        
        self.connector = PooledConnector(dsn=self.db_url)
        self.storage = PostgresGraphStorage(connector=self.connector)
        
        # Componenti interni
        self.git_manager = GitVolumeManager()
        self.builder = KnowledgeGraphBuilder(self.storage)

    def index(self, force: bool = False) -> str:
        """
        Esegue l'indicizzazione completa.
        Gestisce il ciclo di vita dello snapshot e il lock distribuito.
        """
        
        logger.info(f"🚀 Indexing Request Start: {self.repo_url} ({self.branch})")
        
        repo_name = self.repo_url.rstrip('/').split('/')[-1].replace('.git', '')
        repo_id = self.storage.ensure_repository(self.repo_url, self.branch, repo_name)
        
        with tracer.start_as_current_span("indexer.run") as span:
            span.set_attribute("repo.url", self.repo_url)
            span.set_attribute("repo.branch", self.branch)
            span.set_attribute("config.force", force)

            active_snapshot_id = None

            # === GREEDY WORKER LOOP ===
            while True:
                # Fase A: Network Sync
                logger.info("🌍 Syncing repository cache...")
                self.git_manager.ensure_repo_updated(self.repo_url)
                commit = self.git_manager.get_head_commit(self.repo_url, self.branch)
                
                # Fase B: Lock & Concurrency Check
                with tracer.start_as_current_span("indexer.check_snapshot"):
                    snapshot_id, is_new = self.storage.create_snapshot(repo_id, commit, force_new=force)
                
                if not is_new and snapshot_id is None:
                    logger.info("⏸️  Repo occupata, richiesta accodata.")
                    span.set_attribute("status", "queued")
                    return "queued"
                
                if not is_new and snapshot_id and not force:
                    logger.info(f"✅ Snapshot {snapshot_id} già valido.")
                    span.set_attribute("status", "cached")
                    return snapshot_id

                active_snapshot_id = snapshot_id
                
                try:

                    # Fase C: Esecuzione in Ambiente Effimero
                    # 1. Per il Parser (Context Completo: Codice + Test + Docs)
                    # 2. Per SCIP (Core Graph: Solo Codice, Pruned)
                    with ExitStack() as stack:
                        parser_worktree = stack.enter_context(
                            self.git_manager.ephemeral_worktree(self.repo_url, commit)
                        )
                        scip_worktree = stack.enter_context(
                            self.git_manager.ephemeral_worktree(self.repo_url, commit)
                        )
                        
                        logger.info(f"⚙️  Worktrees montati.\n   Parser: {parser_worktree}\n   SCIP: {scip_worktree}")
                        
                        self._run_indexing_pipeline(
                            repo_id=repo_id,
                            snapshot_id=snapshot_id,
                            commit=commit,
                            parser_worktree=parser_worktree, # Passiamo entrambi
                            scip_worktree=scip_worktree
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
                    span.record_exception(e)
                    span.set_status(trace.Status(trace.StatusCode.ERROR))
                    self.storage.fail_snapshot(snapshot_id, str(e))
                    raise e
        
        return active_snapshot_id

    def _run_indexing_pipeline(self, repo_id: str, snapshot_id: str, commit: str, parser_worktree: str, scip_worktree: str):
        """
        Orchestra il parsing parallelo e l'analisi SCIP.
        """
        # Inizializzazione Componenti
        # Nota: SCIPRunner e Indexer lavorano sul path locale
        # scip_runner = SCIPRunner(repo_path=scip_worktree) # SCIP lavora sul path che verrà potato
        scip_indexer = SCIPIndexer(repo_path=scip_worktree)
        previous_live_snapshot = self.storage.get_active_snapshot_id(repo_id)
        current_context = context.get_current()

        # File Discovery
        logger.info("🔍 Scanning files...")
        all_files = []
        IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", "target", "vendor"} # da modificare usare parsing_filters.py
        
        for root, dirs, files in os.walk(parser_worktree):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for file in files:
                _, ext = os.path.splitext(file)
                if ext in {'.py', '.js', '.ts', '.tsx', '.jsx', '.java', '.go', '.rs', '.c', '.cpp', '.php', '.html', '.css'}: 
                    rel_path = os.path.relpath(os.path.join(root, file), parser_worktree)
                    all_files.append(rel_path)

        carrier = {}
        inject(carrier)

        # Configurazione Parallelismo
        total_cpus = multiprocessing.cpu_count()
        num_workers = 5#max(1, total_cpus // 2)
        mp_context = multiprocessing.get_context('spawn')

        TASK_CHUNK_SIZE = 50 
        file_chunks = list(_chunked_iterable(all_files, TASK_CHUNK_SIZE))
        
        logger.info(f"🔨 Parsing & SCIP with {num_workers} workers (Direct DB Mode)...")

        # Funzione helper per SCIP in thread
        def _run_scip_buffered(ctx):
            # 1. Attacca il contesto del padre (Main Thread) a questo Thread
            token = context.attach(ctx)
            try:
                # 2. Avvia lo span ORA che il contesto è corretto
                with tracer.start_as_current_span("scip.binary_execution") as span:
                    try:
                        return list(scip_indexer.stream_relations())
                    except Exception as e:
                        logger.error(f"SCIP Extraction Failed: {e}")
                        span.record_exception(e)
                        span.set_status(trace.Status(trace.StatusCode.ERROR))
                        return []
            finally:
                # 3. Stacca il contesto SOLO quando tutto è finito (fuori dal 'with')
                context.detach(token)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as scip_executor:
            # 1. Avvio SCIP in background (Heavy Process + Streaming)
            future_scip = scip_executor.submit(_run_scip_buffered, current_context)

            # 2. Avvio Parsing Workers (Heavy CPU + DB Write)
            with concurrent.futures.ProcessPoolExecutor(
                max_workers=num_workers, 
                mp_context=mp_context,
                initializer=_init_worker_process,
                initargs=(parser_worktree, snapshot_id, commit, self.repo_url, self.branch, self.db_url,self.worker_telemetry_init)
            ) as executor:
                
                future_to_chunk = {
                    executor.submit(_process_and_insert_chunk, chunk,carrier): chunk 
                    for chunk in file_chunks
                }
                
                total_processed = 0
                completed_chunks = 0

                for future in concurrent.futures.as_completed(future_to_chunk):
                    try:
                        count,_ = future.result()

                        total_processed += count
                        completed_chunks += 1
                        
                        if completed_chunks % 10 == 0:
                            logger.info(f"⏳ Parsed {total_processed}/{len(all_files)} files...")
                            
                    except Exception as e:
                        logger.error(f"❌ Worker Error: {e}")

            # 3. Integrazione SCIP (SQL-Based Resolution)
            logger.info("🔗 Waiting for SCIP relations extraction...")

            with tracer.start_as_current_span("indexer.wait_scip"):
                scip_relations = future_scip.result()
            
            if scip_relations:
                logger.info(f"🔗 Processing {len(scip_relations)} SCIP relations (SQL Batch Mode)...")
                
                # Conversione in Tuple Raw per il DB
                # Formato: (s_path, s_start, s_end, t_path, t_start, t_end, rel_type, meta_json)
                raw_batch = []
                BATCH_SIZE = 10000 # Possiamo osare batch più grandi con COPY
                
                for rel in scip_relations:
                    # Skip se mancano range (es. nodi esterni non risolti/filtrati)
                    if not rel.source_byte_range or not rel.target_byte_range:
                        continue
                        
                    raw_batch.append((
                        rel.source_file,
                        rel.source_byte_range[0],
                        rel.source_byte_range[1],
                        rel.target_file,
                        rel.target_byte_range[0],
                        rel.target_byte_range[1],
                        rel.relation_type,
                        json.dumps(rel.metadata)
                    ))
                    
                    if len(raw_batch) >= BATCH_SIZE:
                        self.storage.ingest_scip_relations(raw_batch, snapshot_id)
                        raw_batch = []
                
                # Flush finale
                if raw_batch:
                    self.storage.ingest_scip_relations(raw_batch, snapshot_id)
            else:
                logger.info("ℹ️ No SCIP relations found.")


        # Attivazione Finale
        current_stats = self.storage.get_stats()
        stats = {
            "files": total_processed, 
            "nodes": current_stats.get("total_nodes", 0),
            "engine": "v9_enterprise_streaming"
        }
        
        manifest_tree = {"type": "dir", "children": {}} 
        
        # 4. RECONSTRUCT MANIFEST FROM DB
        # Poiché usiamo COPY e INSERT raw, il manifest in memoria non è aggiornato.
        # Lo ricostruiamo leggendo i path salvati che sono la source of truth.
        db_files = self.storage.list_file_paths(snapshot_id)
        
        for path in db_files:
            parts = path.split('/')
            curr = manifest_tree
            for part in parts[:-1]:
                if part not in curr["children"]:
                    curr["children"][part] = {"type": "dir", "children": {}}
                curr = curr["children"][part]
            curr["children"][parts[-1]] = {"type": "file"} 
        
        with tracer.start_as_current_span("indexer.activate_snapshot"):
            self.storage.activate_snapshot(repo_id, snapshot_id, stats, manifest=manifest_tree)
            logger.info(f"🚀 SNAPSHOT ACTIVATED: {snapshot_id}")

        if previous_live_snapshot and previous_live_snapshot != snapshot_id:
            logger.info(f"🧹 Pruning old snapshot {previous_live_snapshot}...")
            self.storage.prune_snapshot(previous_live_snapshot)


    def embed(self, provider: EmbeddingProvider, batch_size: int = 32, debug: bool = False, force_snapshot_id: str = None):
        """
        Genera embeddings.
        NOTA: In architettura Enterprise, questo metodo dovrebbe essere chiamato da un worker separato.
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