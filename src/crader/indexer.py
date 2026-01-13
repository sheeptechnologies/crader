import os

os.environ["GRPC_ENABLE_FORK_SUPPORT"] = "0"
os.environ["GRPC_POLL_STRATEGY"] = "poll"
import concurrent.futures
import gc
import itertools
import json
import logging
import multiprocessing
from contextlib import ExitStack
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple

from opentelemetry import context, trace
from opentelemetry.propagate import extract, inject

tracer = trace.get_tracer(__name__)

# Internal Components
from .embedding.embedder import CodeEmbedder
from .graph.builder import KnowledgeGraphBuilder
from .graph.indexers.scip import SCIPIndexer
from .parsing.parser import TreeSitterRepoParser
from .providers.embedding import EmbeddingProvider
from .storage.connector import PooledConnector, SingleConnector
from .storage.postgres import PostgresGraphStorage
from .volume_manager.git_volume_manager import GitVolumeManager

logger = logging.getLogger(__name__)

# ==============================================================================
#  WORKER FUNCTIONS (ISOLATED CONTEXT)
# ==============================================================================

_worker_parser = None
_worker_storage = None
_worker_builder = None


def _init_worker_process(
    worktree_path: str,
    snapshot_id: str,
    commit_hash: str,
    repo_url: str,
    branch: str,
    db_url: str,
    worker_init_fn: Optional[Callable],
):
    """
    Bootstrap entry point for worker processes in the indexing pipeline.

    Initializes a localized environment for the worker, including:
    1.  **Parser Initialization**: Sets up a `TreeSitterRepoParser` instance attached to the
        specific `worktree_path`. This parser is responsible for granular analysis of source files.
    2.  **Storage Access**: Establishes a *direct*, dedicated `PostgresGraphStorage` connection
        using `SingleConnector`. This ensures independent I/O handling for the worker, avoiding
        contention with the main process's connection pool.

    The global variables `_worker_parser` and `_worker_storage` are populated here to be accessed relative to
    the process state.

    Args:
        worktree_path (str): The filesystem path to the ephemeral worktree where the repository files are checked out.
        snapshot_id (str): The unique identifier for the current indexing snapshot.
        commit_hash (str): The SHA-1 hash of the commit being indexed.
        repo_url (str): The origin URL of the repository.
        branch (str): The branch name currently being processed.
        db_url (str): The database connection string (DSN).
        worker_init_fn (Optional[Callable]): An optional hook for custom worker initialization (e.g., telemetry setup).
    """
    if worker_init_fn:
        try:
            worker_init_fn()
        except Exception as e:
            print(f"⚠️ [WORKER INIT] Custom telemetry setup failed: {e}")

    global _worker_parser, _worker_storage, _worker_builder

    # 1. Init Parser (Cpu Bound)
    _worker_parser = TreeSitterRepoParser(repo_path=worktree_path)
    _worker_parser.snapshot_id = snapshot_id
    _worker_parser.repo_info = {
        "commit_hash": commit_hash,
        "url": repo_url,
        "branch": branch,
        "name": repo_url.split("/")[-1],
    }

    # 2. Init Storage (I/O Bound - Direct Connection)
    from .storage.postgres import PostgresGraphStorage

    try:
        connector = SingleConnector(dsn=db_url)
        _worker_storage = PostgresGraphStorage(connector=connector)
        _worker_builder = KnowledgeGraphBuilder(_worker_storage)
    except Exception as e:
        print(f"❌ [WORKER INIT ERROR] DB Connect failed: {e}")
        _worker_storage = None
        _worker_builder = None


def _process_and_insert_chunk(file_paths: List[str], carrier: Dict[str, str]) -> Tuple[int, Dict[str, float]]:
    """
    Worker routine to process a batch of files.

    This function is the core unit of work executed by parallel workers. It performs the following steps:
    1.  **Context Extraction**: Restores the distributed tracing context from the `carrier`.
    2.  **Buffers Initialization**: Sets up local buffers for files, nodes, contents, and relationships to batch DB writes.
    3.  **Parsing Loops**: Iterates through the assigned `file_paths`:
        *   Invokes `_worker_parser.stream_semantic_chunks` to parse the file.
        *   Accumulates the resulting FileRecords, ChunkNodes, Content, and Relations into the local buffers.
    4.  **Batch Flashing**: Periodically (based on `BATCH_SIZE`) flushes the accumulated data to the database via `_worker_storage`.
    5.  **Error Handling**: Catches frame-level exceptions, logs warnings for unparsable files, and ensures robust execution.

    Args:
        file_paths (List[str]): A list of relative paths to the files assigned to this worker.
        carrier (Dict[str, str]): The tracing context propagation carrier.

    Returns:
        Tuple[int, Dict[str, float]]: A tuple containing the count of successfully processed files and an empty metrics dictionary (reserved for future use).
    """
    gc.disable()
    global _worker_parser, _worker_storage, _worker_builder
    if not _worker_storage or not _worker_builder:
        return 0, {}

    ctx = extract(carrier)
    BATCH_SIZE_NODES = 50000
    BATCH_SIZE_FILES = 500

    buffer = {"files": [], "nodes": [], "contents": [], "rels": [], "fts": []}
    processed_count = 0

    def flush_buffers():
        if not (buffer["files"] or buffer["nodes"]):
            return
        try:
            with tracer.start_as_current_span("worker.db_flush") as db_span:
                db_span.set_attribute("nodes.count", len(buffer["nodes"]))
                db_span.set_attribute("files.count", len(buffer["files"]))
                if buffer["files"]:
                    _worker_storage.add_files_raw(buffer["files"])
                if buffer["contents"]:
                    _worker_storage.add_contents_raw(buffer["contents"])
                if buffer["nodes"]:
                    _worker_storage.add_nodes_raw(buffer["nodes"])
                if buffer["rels"]:
                    _worker_storage.add_relations_raw(buffer["rels"])

                # Flush Full-Text Search (FTS) entries *after* nodes to ensure referential integrity.
                if buffer["fts"]:
                     _worker_storage.add_search_index(buffer["fts"])

            buffer["files"].clear()
            buffer["nodes"].clear()
            buffer["contents"].clear()
            buffer["rels"].clear()
            buffer["fts"].clear()
        except Exception as e:
            logger.error(f"❌ [WORKER FLUSH ERROR] {e}")
            raise e

    with tracer.start_as_current_span("worker.process_chunk", context=ctx) as span:
        span.set_attribute("chunk.total_files", len(file_paths))
        span.set_attribute("process.pid", os.getpid())

        for f_path in file_paths:
            try:
                for f_rec, nodes, contents, rels in _worker_parser.stream_semantic_chunks(file_list=[f_path]):
                    buffer["files"].append(
                        (
                            f_rec.id,
                            f_rec.snapshot_id,
                            f_rec.commit_hash,
                            f_rec.file_hash,
                            f_rec.path,
                            f_rec.language,
                            f_rec.size_bytes,
                            f_rec.category,
                            f_rec.indexed_at,
                            f_rec.parsing_status,
                            f_rec.parsing_error,
                        )
                    )
                    for n in nodes:
                        bs, be = n.byte_range
                        buffer["nodes"].append(
                            (
                                n.id,
                                n.file_id,
                                n.file_path,
                                n.start_line,
                                n.end_line,
                                bs,
                                be,
                                n.chunk_hash,
                                be - bs,
                                json.dumps(n.metadata),
                            )
                        )
                    for c in contents:
                        buffer["contents"].append((c.chunk_hash, c.content))
                    for r in rels:
                        buffer["rels"].append((r.source_id, r.target_id, r.relation_type, json.dumps(r.metadata)))

                    # Buffer FTS documents for batch insertion.
                    # We defer insertion to the flush phase to ensure nodes exist first.
                    if nodes and contents:
                         content_map = {c.chunk_hash: c for c in contents}
                         fts_docs = _worker_builder.build_search_documents(nodes, content_map)
                         buffer["fts"].extend(fts_docs)

                    processed_count += 1
                    if len(buffer["nodes"]) >= BATCH_SIZE_NODES or len(buffer["files"]) >= BATCH_SIZE_FILES:
                        with tracer.start_as_current_span("worker.flush_buffers", context=ctx):
                            flush_buffers()
            except Exception as e:
                span.record_exception(e)
                logger.warning(f"⚠️ Skipping {f_path}: {e}")
                continue

        flush_buffers()
        return processed_count, {}


def _chunked_iterable(iterable, size):
    it = iter(iterable)
    while True:
        chunk = tuple(itertools.islice(it, size))
        if not chunk:
            break
        yield chunk


# ==============================================================================
#  MAIN CLASS (ORCHESTRATOR)
# ==============================================================================


class CodebaseIndexer:
    """
    The High-Level Orchestrator for the Codebase Indexing Pipeline.

    This class serves as the central control unit for ingesting, parsing, and indexing a source code repository.
    It manages the end-to-end lifecycle of the indexing process, coordinating:

    *   **Repository Synchronization**: Using `GitVolumeManager` to fetch and update the local code cache.
    *   **Snapshot Management**: creating, validating, and activating snapshots in the `GraphStorage`.
    *   **Parallel Parsing**: Distributing the parsing workload across multiple worker processes using `ProcessPoolExecutor`.
    *   **SCIP Analysis**: Integrating SCIP (Stack Graph) analysis for precise code intelligence and cross-file references.
    *   **Graph Construction**: Orchestrating the storage of nodes, edges, and semantic metadata into the PostgreSQL backend.

    Attributes:
        repo_url (str): The URL of the repository being indexed.
        branch (str): The target branch name.
        db_url (str): The database connection string.
        storage (PostgresGraphStorage): The storage interface for graph data persistence.
        git_manager (GitVolumeManager): The manager for git operations and worktrees.
        builder (KnowledgeGraphBuilder): Helper for graph construction logic.
    """

    def __init__(
        self,
        repo_url: str,
        branch: str,
        db_url: Optional[str] = None,
        worker_telemetry_init: Optional[Callable[[], None]] = None,
    ):
        """
        Initializes the CodebaseIndexer.

        Args:
            repo_url (str): The URL of the repository to index.
            branch (str): The specific branch to index (e.g., 'main').
            db_url (Optional[str]): Database connection string. If None, it attempts to load from `DB_URL` env var.
            worker_telemetry_init (Optional[Callable]): Optional callback to initialize telemetry in worker processes.

        Raises:
            ValueError: If `db_url` is not provided and `DB_URL` environment variable is missing.
        """
        self.repo_url = repo_url
        self.branch = branch
        self.worker_telemetry_init = worker_telemetry_init

        self.db_url = db_url or os.getenv("CRADER_DB_URL")
        if not self.db_url:
            raise ValueError("DB_URL non fornito e non trovato nelle variabili d'ambiente.")

        safe_log_url = self.db_url.split("@")[-1] if "@" in self.db_url else "..."
        logger.info(f"🔌 Connecting to DB (Pool): {safe_log_url}")

        self.connector = PooledConnector(dsn=self.db_url)
        self.storage = PostgresGraphStorage(connector=self.connector)

        self.git_manager = GitVolumeManager()
        self.builder = KnowledgeGraphBuilder(self.storage)

    def index(self, force: bool = False, auto_prune: bool = False) -> str:
        """
        Executes the full indexing pipeline: Repository Sync -> Parsing + SCIP -> Activation.

        This method encapsulates the core logic for checking the repository state and performing the indexing if necessary.

        **Workflow:**
        1.  **Repository Sync**: Ensures the local cache of the repository is up-to-date with the remote.
        2.  **Snapshot Creation**: Checks if a snapshot for the current commit already exists.
            *   If yes and `force` is False, returns the existing snapshot ID.
            *   If no (or `force` is True), creates a new snapshot with status 'indexing'.
        3.  **Worktree Setup**: Creates ephemeral worktrees for isolated parsing and SCIP analysis.
        4.  **Pipeline Execution**: Invokes `_run_indexing_pipeline` to perform the heavy lifting (parsing, SCIP extraction, DB ingestion).
        5.  **Completion & Activation**:
            *   On success: Marks the snapshot as 'completed', activates it (updating the repository's current pointer), and generates the file manifest.
            *   On failure: Marks the snapshot as 'failed' and re-raises the exception.

        Args:
            force (bool): If True, bypasses the check for existing snapshots and forces a re-index.
            auto_prune (bool): If True, attempts to prune old snapshots after successful indexing.
                               Defaults to False to allow for subsequent embedding processes on historical data.

        Returns:
            str: The ID of the active snapshot (either newly created or existing).

        Returns "queued" if the repository is currently locked/busy.
        """

        logger.info(f"🚀 Indexing Request Start: {self.repo_url} ({self.branch})")

        repo_name = self.repo_url.rstrip("/").split("/")[-1].replace(".git", "")
        repo_id = self.storage.ensure_repository(self.repo_url, self.branch, repo_name)

        with tracer.start_as_current_span("indexer.run") as span:
            span.set_attribute("repo.url", self.repo_url)
            span.set_attribute("repo.branch", self.branch)
            span.set_attribute("config.force", force)

            active_snapshot_id = None

            while True:
                logger.info("🌍 Syncing repository cache...")
                self.git_manager.ensure_repo_updated(self.repo_url)
                commit = self.git_manager.get_head_commit(self.repo_url, self.branch)

                with tracer.start_as_current_span("indexer.check_snapshot"):
                    snapshot_id, is_new = self.storage.create_snapshot(repo_id, commit, force_new=force)

                if not is_new and snapshot_id is None:
                    logger.info("⏸️  Repo occupata, richiesta accodata.")
                    return "queued"

                if not is_new and snapshot_id and not force:
                    logger.info(f"✅ Snapshot {snapshot_id} già valido.")
                    return snapshot_id

                active_snapshot_id = snapshot_id

                try:
                    with ExitStack() as stack:
                        parser_worktree = stack.enter_context(
                            self.git_manager.ephemeral_worktree(self.repo_url, commit)
                        )
                        scip_worktree = stack.enter_context(self.git_manager.ephemeral_worktree(self.repo_url, commit))

                        logger.info("⚙️  Worktrees montati.")

                        self._run_indexing_pipeline(
                            repo_id=repo_id,
                            snapshot_id=snapshot_id,
                            commit=commit,
                            parser_worktree=parser_worktree,
                            scip_worktree=scip_worktree,
                        )
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

        # [FIX] Optional Pruning. Default False to allow incremental embedding.
        if auto_prune:
            self.prune_old_snapshots(repo_id, keep_snapshot_id=active_snapshot_id)

        return active_snapshot_id

    def _run_indexing_pipeline(
        self, repo_id: str, snapshot_id: str, commit: str, parser_worktree: str, scip_worktree: str
    ):
        """
        Internal engine driving the parsing and analysis pipeline.

        This method orchestrates the parallel execution of file parsing and the concurrent execution of SCIP analysis.

        **Components:**
        *   **File Enumeration**: Scans the `parser_worktree` to identify relevant source files, respecting ignore patterns.
        *   **Parallel Parsing**: Uses a `ProcessPoolExecutor` to spawn worker processes that parse files in chunks and ingest them into the DB.
        *   **SCIP Analysis**: concurrently runs the `SCIPIndexer` in a separate thread to extract cross-file relationship data.
        *   **Normalization**: Merges SCIP data with the parsed nodes to create a rich property graph (resolved edges).
        *   **Snapshot Finalization**: Compiles statistics and generates the file structure manifest before activating the snapshot.

        Args:
            repo_id (str): The internal ID of the repository.
            snapshot_id (str): The ID of the current snapshot being populated.
            commit (str): The commit hash.
            parser_worktree (str): Path to the worktree dedicated to AST parsing.
            scip_worktree (str): Path to the worktree dedicated to SCIP analysis.
        """
        scip_indexer = SCIPIndexer(repo_path=scip_worktree)
        current_context = context.get_current()

        logger.info("🔍 Scanning files...")
        all_files = []
        IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", "target", "vendor"}

        for root, dirs, files in os.walk(parser_worktree):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for file in files:
                _, ext = os.path.splitext(file)
                if ext in {
                    ".py",
                    ".js",
                    ".ts",
                    ".tsx",
                    ".jsx",
                    ".java",
                    ".go",
                    ".rs",
                    ".c",
                    ".cpp",
                    ".php",
                    ".html",
                    ".css",
                }:
                    rel_path = os.path.relpath(os.path.join(root, file), parser_worktree)
                    all_files.append(rel_path)

        carrier = {}
        inject(carrier)

        total_cpus = multiprocessing.cpu_count()
        num_workers = 5  # [TODO] Adjust based on system resources
        mp_context = multiprocessing.get_context("spawn")
        file_chunks = list(_chunked_iterable(all_files, 50))

        logger.info(f"🔨 Parsing & SCIP with {num_workers} workers...")

        def _run_scip_buffered(ctx):
            token = context.attach(ctx)
            try:
                with tracer.start_as_current_span("scip.binary_execution") as span:
                    try:
                        return list(scip_indexer.stream_relations())
                    except Exception as e:
                        logger.error(f"SCIP Extraction Failed: {e}")
                        return []
            finally:
                context.detach(token)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as scip_executor:
            future_scip = scip_executor.submit(_run_scip_buffered, current_context)

            with concurrent.futures.ProcessPoolExecutor(
                max_workers=num_workers,
                mp_context=mp_context,
                initializer=_init_worker_process,
                initargs=(
                    parser_worktree,
                    snapshot_id,
                    commit,
                    self.repo_url,
                    self.branch,
                    self.db_url,
                    self.worker_telemetry_init,
                ),
            ) as executor:
                future_to_chunk = {
                    executor.submit(_process_and_insert_chunk, chunk, carrier): chunk for chunk in file_chunks
                }

                total_processed = 0
                completed_chunks = 0
                for future in concurrent.futures.as_completed(future_to_chunk):
                    try:
                        count, _ = future.result()
                        total_processed += count
                        completed_chunks += 1
                        if completed_chunks % 10 == 0:
                            logger.info(f"⏳ Parsed {total_processed}/{len(all_files)} files...")
                    except Exception as e:
                        logger.error(f"❌ Worker Error: {e}")

            logger.info("🔗 Waiting for SCIP relations extraction...")
            scip_relations = future_scip.result()

            if scip_relations:
                logger.info(f"🔗 Processing {len(scip_relations)} SCIP relations (SQL Batch Mode)...")
                raw_batch = []
                BATCH_SIZE = 10000
                for rel in scip_relations:
                    if not rel.source_byte_range or not rel.target_byte_range:
                        continue
                    raw_batch.append(
                        (
                            rel.source_file,
                            rel.source_byte_range[0],
                            rel.source_byte_range[1],
                            rel.target_file,
                            rel.target_byte_range[0],
                            rel.target_byte_range[1],
                            rel.relation_type,
                            json.dumps(rel.metadata),
                        )
                    )
                    if len(raw_batch) >= BATCH_SIZE:
                        self.storage.ingest_scip_relations(raw_batch, snapshot_id)
                        raw_batch = []
                if raw_batch:
                    self.storage.ingest_scip_relations(raw_batch, snapshot_id)

        current_stats = self.storage.get_stats()
        stats = {
            "files": total_processed,
            "nodes": current_stats.get("total_nodes", 0),
            "engine": "v9_enterprise_streaming",
        }

        manifest_tree = {"type": "dir", "children": {}}
        db_files = self.storage.list_file_paths(snapshot_id)
        for path in db_files:
            parts = path.split("/")
            curr = manifest_tree
            for part in parts[:-1]:
                if part not in curr["children"]:
                    curr["children"][part] = {"type": "dir", "children": {}}
                curr = curr["children"][part]
            curr["children"][parts[-1]] = {"type": "file"}

        with tracer.start_as_current_span("indexer.activate_snapshot"):
            self.storage.activate_snapshot(repo_id, snapshot_id, stats, manifest=manifest_tree)
            logger.info(f"🚀 SNAPSHOT ACTIVATED: {snapshot_id}")

    async def embed(
        self, provider: EmbeddingProvider, batch_size: int = 1000, mock_api: bool = False, force_snapshot_id: str = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Triggers the Asynchronous Embedding Pipeline.

        This method delegates the generation of vector embeddings to the `CodeEmbedder`. It operates as a generator,
        yielding status updates throughout the process to allow the caller to track progress.

        **Key Features:**
        *   **Asynchronous Processing**: Uses asyncio to handle concurrent API calls efficiently.
        *   **Staging & Deduplication**: The pipeline (managed by `CodeEmbedder`) includes phases for staging vectors
            and deduplicating against existing vectors to minimize API costs.
        *   **Incremental Updates**: Only new or changed code blocks are sent for embedding.

        Args:
            provider (EmbeddingProvider): The provider (e.g., OpenAI) to use for generating embeddings.
            batch_size (int): Number of items to process in a single batch.
            mock_api (bool): If True, simulates API calls (for testing/debugging).
            force_snapshot_id (Optional[str]): If provided, forces embedding of a specific snapshot ID instead of the currently active one.

        Yields:
            Dict[str, Any]: Status update dictionaries containing keys like "status", "progress", etc.

        Raises:
            ValueError: If no active snapshot is found or specified.
        """
        logger.info(f"🤖 Embedding Start: {provider.model_name} (Async Mode)")

        repo_name = self.repo_url.rstrip("/").split("/")[-1].replace(".git", "")
        repo_id = self.storage.ensure_repository(self.repo_url, self.branch, repo_name)

        target_snapshot_id = force_snapshot_id or self.storage.get_active_snapshot_id(repo_id)
        if not target_snapshot_id:
            raise ValueError("No active snapshot found. Run index() first.")

        embedder = CodeEmbedder(self.storage, provider)

        async for status_update in embedder.run_indexing(
            snapshot_id=target_snapshot_id, batch_size=batch_size, mock_api=mock_api
        ):
            yield status_update

    def prune_old_snapshots(self, repo_id: str, keep_snapshot_id: str):
        """
        Explicit method to clean up old snapshots.
        MUST be called ONLY after embedding has successfully completed.
        """
        # Find all completed snapshots different from the one to keep
        # This logic should be better implemented in storage, here we make a direct call for brevity
        # We assume storage.prune_snapshot(id) works.

        # For now, in this PoC, we make a direct query if possible, or trust the parameter
        # In real design we should have storage.list_snapshots(repo_id) -> iterate -> prune

        # Here we use simple logic: Prune what was active before (if we know it)
        # But since index() does not return the old ID, we can't do it easily here.
        # Required modification in storage layer for "prune_all_except(repo_id, keep_id)"

        # Placeholder: For current test it's not critical to implement full auto cleanup,
        # the important thing is NOT to delete prematurely.
        logger.info(f"🧹 [Manual Prune] Request to prune old snapshots for repo {repo_id} (keeping {keep_snapshot_id})")
        # TODO: Implement storage.prune_all_except(repo_id, keep_snapshot_id)
        pass

    def get_stats(self):
        return self.storage.get_stats()

    def close(self):
        if hasattr(self, "storage"):
            self.storage.close()
