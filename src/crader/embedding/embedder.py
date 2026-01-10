import asyncio
import datetime
import hashlib
import json
import logging
import os
import random
import uuid
from concurrent.futures import ProcessPoolExecutor
from typing import Any, AsyncGenerator, Dict, List, Tuple

from ..providers.embedding import EmbeddingProvider
from ..storage.base import GraphStorage

logger = logging.getLogger(__name__)

# --- CPU BOUND TASKS ---


def _compute_prompt_and_hash(node: Dict[str, Any]) -> Tuple[str, str]:
    """
    Constructs the semantic context window and its deterministic hash.

    This function prepares the "prompt" that will be sent to the embedding model.
    It combines:
    1.  **Strict Context**: File path, language, category.
    2.  **Semantic Metadata**: Roles (e.g., 'class', 'method') and other tags derived from parsing.
    3.  **Cross-References**: Incoming definition symbols (e.g., if this node defines `User`, and `User` is imported elsewhere).
    4.  **Code Content**: The actual source code of the chunk.

    The combination is hashed (SHA-256) to create a unique fingerprint (`v_hash`).
    This fingerprint enables identifying identical code blocks across commits to skip re-embedding.

    Args:
        node (Dict[str, Any]): The node data dictionary.

    Returns:
        Tuple[str, str]: A tuple containing the formatted prompt string and its hex digest hash.
    """
    lang = node.get("language", "text")
    category = node.get("category", "unknown")
    content = node.get("content", "")
    definitions = node.get("incoming_definitions", [])

    meta_json = node.get("metadata_json")
    meta = {}
    if meta_json:
        try:
            meta = json.loads(meta_json)
        except:
            pass

    context_parts = ["[CONTEXT]", f"File: {node.get('file_path')}", f"Language: {lang}", f"Category: {category}"]

    matches = meta.get("semantic_matches", [])
    roles = [m.get("label") or m.get("value", "").replace("_", " ") for m in matches if m.get("category") == "role"]
    others = [
        m.get("label") or m.get("value", "").replace("_", " ")
        for m in matches
        if m.get("category") not in ("role", "type")
    ]

    if roles:
        context_parts.append(f"Role: {', '.join(roles)}")
    if others:
        context_parts.append(f"Tags: {', '.join(others)}")

    if definitions:
        symbols_str = ", ".join(sorted(set(definitions)))
        context_parts.append(f"Defines: {symbols_str}")

    context_parts.append(f"\n[CODE]\n{content}")
    full_text = "\n".join(context_parts)

    v_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()

    return full_text, v_hash


def _prepare_batch_for_staging(nodes: List[Dict], model_name: str, snapshot_id: str) -> List[Tuple]:
    prepared_rows = []
    for node in nodes:
        full_text, v_hash = _compute_prompt_and_hash(node)
        row = (
            str(uuid.uuid4()),  # id
            node["id"],  # chunk_id
            snapshot_id,  # snapshot_id
            v_hash,  # vector_hash
            node.get("file_path"),
            node.get("language"),
            node.get("category"),
            node.get("start_line"),
            node.get("end_line"),
            model_name,
            full_text,  # content
        )
        prepared_rows.append(row)
    return prepared_rows


class CodeEmbedder:
    """
    Asynchronous Vector Embedding Engine.

    This class manages the high-throughput generation of vector embeddings for code chunks.
    It implements a sophisticated "Staging -> Deduplication -> Delta Computing" pipeline to maximize efficiency and minimize API costs.

    **Pipeline Stages:**
    1.  **Preparation (CPU Bound)**: "Hydrates" nodes with their content and metadata, constructs prompts, and computes SHA-256 hashes.
        *   *Optimization*: Uses `ProcessPoolExecutor` to avoid blocking the main asyncio event loop.
    2.  **Staging (I/O Bound)**: Bulk loads prepared data into an UNLOGGED staging table in PostgreSQL.
    3.  **Deduplication (SQL Bound)**: Compares staging hashes with historical `node_embeddings`. If a match is found, the old vector is reused (Cost = $0).
    4.  **Delta Calculation (Net/IO Bound)**: Identifies unmatched (new/changed) nodes and pushes them to a `work_queue`.
    5.  **Parallel Embedding (Network Bound)**: Spawns multiple worker tasks to consume the queue and call the external Embedding Provider (e.g., OpenAI) concurrently.
    6.  **Finalization**: Promotes new vectors to the main table and cleans up.

    Attributes:
        storage (GraphStorage): Persistent storage interface.
        provider (EmbeddingProvider): External AI service wrapper.
        process_pool (ProcessPoolExecutor): Thread pool for CPU-intensive hashing operations.
    """

    def __init__(self, storage: GraphStorage, provider: EmbeddingProvider):
        self.storage = storage
        self.provider = provider
        # Executor to offload CPU tasks (hashing and string manipulation)
        self.process_pool = ProcessPoolExecutor(max_workers=min(4, os.cpu_count() or 1))

    async def run_indexing(
        self, snapshot_id: str, batch_size: int = 1000, mock_api: bool = False
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Executes the full embedding pipeline for a specific snapshot.

        This is a generator method that yields real-time progress updates.

        Args:
            snapshot_id (str): The target snapshot to embed.
            batch_size (int): Size of chunks for database reads/writes.
            mock_api (bool): If True, replaces actual API calls with random vector generation (for testing).

        Yields:
            Dict[str, Any]: Status updates, e.g., `{'status': 'staging_progress', 'staged': 500}`.
        """
        logger.info(f"🚀 Starting Async Indexing for snapshot {snapshot_id} (Mock={mock_api})")

        try:
            # 1. SETUP STAGING
            yield {"status": "init", "message": "Preparing staging environment..."}
            if hasattr(self.storage, "prepare_embedding_staging"):
                self.storage.prepare_embedding_staging()

            # 2. PRODUCER PHASE (DB -> Staging)
            yield {"status": "staging_start", "message": "Streaming enriched nodes from DB..."}

            nodes_iter = self.storage.get_nodes_to_embed(
                snapshot_id=snapshot_id, model_name=self.provider.model_name, batch_size=batch_size
            )

            loop = asyncio.get_running_loop()
            current_batch = []
            total_staged = 0

            for node in nodes_iter:
                current_batch.append(node)
                if len(current_batch) >= batch_size:
                    await self._process_and_stage_batch(current_batch, snapshot_id, loop)
                    total_staged += len(current_batch)
                    yield {"status": "staging_progress", "staged": total_staged}
                    current_batch = []

            if current_batch:
                await self._process_and_stage_batch(current_batch, snapshot_id, loop)
                total_staged += len(current_batch)

            yield {"status": "staging_complete", "total_staged": total_staged}

            # 3. DEDUPLICATION PHASE
            yield {"status": "deduplicating", "message": "Backfilling from history..."}
            recovered_count = 0
            if hasattr(self.storage, "backfill_staging_vectors"):
                recovered_count = self.storage.backfill_staging_vectors(snapshot_id)
            yield {"status": "deduplication_stats", "recovered": recovered_count}

            # 4. FLUSH HITS
            flushed_hits = 0
            if hasattr(self.storage, "flush_staged_hits"):
                flushed_hits = self.storage.flush_staged_hits(snapshot_id)
            yield {"status": "flushed_hits", "count": flushed_hits}

            # 5. DELTA PHASE (Parallel Workers)
            yield {"status": "embedding_start", "message": "Processing new vectors (Delta)..."}

            delta_processed = 0

            if hasattr(self.storage, "fetch_staging_delta"):
                # Code to manage parallel work
                # work_queue: contains batches to process
                work_queue = asyncio.Queue(maxsize=self.provider.max_concurrency * 2)
                # result_queue: to communicate progress to the main thread
                result_queue = asyncio.Queue()

                # A. Producer Task (DB -> Work Queue)
                producer_task = asyncio.create_task(
                    self._delta_producer(snapshot_id, work_queue, batch_size=200)  # Optimized batch for API
                )

                # B. Consumer Workers (Work Queue -> API -> DB -> Result Queue)
                num_workers = getattr(self.provider, "max_concurrency", 5)
                workers = [
                    asyncio.create_task(self._delta_worker(snapshot_id, work_queue, result_queue, mock_api))
                    for _ in range(num_workers)
                ]

                # C. Main Loop: Consumes results and yields
                # Stay listening until all workers are done
                workers_done_count = 0

                while workers_done_count < num_workers:
                    res = await result_queue.get()

                    if res is None:  # Worker completion signal
                        workers_done_count += 1
                    elif isinstance(res, Exception):
                        logger.error(f"Worker Error: {res}")
                        # We don't crash everything, but we might want to flag it
                    else:
                        # res is an integer (number of items processed)
                        delta_processed += res
                        yield {"status": "embedding_progress", "current_batch": res, "total_embedded": delta_processed}

                    result_queue.task_done()

                await producer_task  # Ensure the producer finished cleanly

            yield {
                "status": "completed",
                "total_nodes": total_staged,
                "recovered_from_history": recovered_count,
                "newly_embedded": delta_processed,
            }

        finally:
            # FINAL CLEANUP
            if hasattr(self.storage, "cleanup_staging"):
                self.storage.cleanup_staging(snapshot_id)

    async def _process_and_stage_batch(self, nodes: List[Dict], snapshot_id: str, loop):
        """Helper for staging phase (CPU + IO)"""
        prepared_data = await loop.run_in_executor(
            self.process_pool, _prepare_batch_for_staging, nodes, self.provider.model_name, snapshot_id
        )
        if hasattr(self.storage, "load_staging_data"):
            await loop.run_in_executor(None, self.storage.load_staging_data, iter(prepared_data))

    async def _delta_producer(self, snapshot_id: str, work_queue: asyncio.Queue, batch_size: int):
        """
        Producer Coroutine: Feeds the Worker Queue.

        Streams pending items from the staging table (those that missed the deduplication cache)
        and places them into the `work_queue` for consumers.
        """
        try:
            delta_gen = self.storage.fetch_staging_delta(snapshot_id, batch_size=batch_size)
            for batch in delta_gen:
                await work_queue.put(batch)
        except Exception as e:
            logger.error(f"Producer Error: {e}")
        finally:
            # Send stop signal for each worker
            num_workers = getattr(self.provider, "max_concurrency", 5)
            for _ in range(num_workers):
                await work_queue.put(None)

    async def _delta_worker(
        self, snapshot_id: str, work_queue: asyncio.Queue, result_queue: asyncio.Queue, mock_api: bool
    ):
        """
        Consumer Worker Coroutine: Processes Embedding Batches.

        1.  Pulls a batch of text from `work_queue`.
        2.  Invokes `provider.embed_async` (high latency).
        3.  Saves the resulting vectors to the database.
        4.  Pushes metrics to `result_queue`.
        """
        try:
            while True:
                batch = await work_queue.get()
                if batch is None:
                    work_queue.task_done()
                    break

                try:
                    prompts = [item["content"] for item in batch]

                    # REAL ASYNC CALL
                    if mock_api:
                        vectors = await self._mock_embed_async(prompts)
                    else:
                        # Parallel magic happens here
                        vectors = await self.provider.embed_async(prompts)

                    # Preparazione salvataggio
                    records_to_save = []
                    for item, vec in zip(batch, vectors):
                        records_to_save.append(
                            {
                                "id": item["id"],
                                "chunk_id": item["chunk_id"],
                                "snapshot_id": snapshot_id,
                                "vector_hash": item["vector_hash"],
                                "model_name": self.provider.model_name,
                                "created_at": datetime.datetime.utcnow(),
                                # Metadati denormalizzati
                                "file_path": item.get("file_path"),
                                "language": item.get("language"),
                                "category": item.get("category"),
                                "start_line": item.get("start_line"),
                                "end_line": item.get("end_line"),
                                "embedding": vec,
                            }
                        )

                    # Direct Write to DB (IO Bound but fast in batch)
                    if hasattr(self.storage, "save_embeddings_direct"):
                        self.storage.save_embeddings_direct(records_to_save)
                    else:
                        self.storage.save_embeddings(records_to_save)

                    # Signal success
                    await result_queue.put(len(records_to_save))

                except Exception as e:
                    logger.error(f"Error in embedding worker: {e}")
                    await result_queue.put(e)
                finally:
                    work_queue.task_done()
        finally:
            # Signal that this worker is dying
            await result_queue.put(None)

    async def _mock_embed_async(self, texts: List[str]) -> List[List[float]]:
        latency = random.uniform(0.05, 0.2)
        await asyncio.sleep(latency)
        dim = self.provider.dimension
        return [[random.random() for _ in range(dim)] for _ in texts]
