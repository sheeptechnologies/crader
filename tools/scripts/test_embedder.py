import asyncio
import hashlib
import logging
import os
import random
import sys
import uuid
from typing import List

# --- SETUP PATH ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, ".."))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from crader.embedding.embedder import CodeEmbedder  # noqa: E402
from crader.models import ChunkContent, ChunkNode, FileRecord  # noqa: E402
from crader.providers.embedding import EmbeddingProvider  # noqa: E402
from crader.storage.connector import PooledConnector  # noqa: E402
from crader.storage.postgres import PostgresGraphStorage  # noqa: E402

# --- CONFIGURAZIONE DB ---
DB_PORT = "6432"
DB_USER = "sheep_user"
DB_PASS = "sheep_password"
DB_NAME = "sheep_index"
DB_DSN = f"postgresql://{DB_USER}:{DB_PASS}@localhost:{DB_PORT}/{DB_NAME}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TEST_PIPELINE")

class MockProvider(EmbeddingProvider):
    def __init__(self, dim=1536):
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return "mock-v1-enterprise"

    def embed(self, texts: List[str]) -> List[List[float]]:
        return [[random.random() for _ in range(self._dim)] for _ in texts]

    async def embed_async(self, texts: List[str]) -> List[List[float]]:
        await asyncio.sleep(0.005)
        return [[random.random() for _ in range(self._dim)] for _ in texts]

def clean_database():
    logger.info("🧹 Cleaning Database...")
    try:
        connector = PooledConnector(dsn=DB_DSN, min_size=1, max_size=1)
        with connector.get_connection() as conn:
            conn.execute("TRUNCATE repositories CASCADE")
        connector.close()
    except Exception as e:
        logger.error(f"⚠️ Errore pulizia DB: {e}")

async def main():
    clean_database()

    logger.info("🔌 Connecting to DB...")
    connector = PooledConnector(dsn=DB_DSN, min_size=4, max_size=10)
    storage = PostgresGraphStorage(connector)
    provider = MockProvider()

    embedder = CodeEmbedder(storage, provider)

    try:
        # ==========================================
        # FASE 1: SNAPSHOT INIZIALE
        # ==========================================
        logger.info("\n=== PHASE 1: Initial Snapshot (Cold Start) ===")
        repo_id = storage.ensure_repository("http://test.enterprise.git", "main", "test-repo")
        snap1_id, _ = storage.create_snapshot(repo_id, "commit-initial", force_new=True)

        NUM_NODES_1 = 1000
        logger.info(f"📥 Seeding DB with {NUM_NODES_1} nodes...")

        files = []
        nodes = []
        contents = []
        content_hashes_map = {}

        for i in range(NUM_NODES_1):
            fid = str(uuid.uuid4())
            cid = str(uuid.uuid4())
            content_str = f"def function_{i}():\n    return 'business_logic_{i}'"
            chash = hashlib.sha256(content_str.encode()).hexdigest()
            content_hashes_map[i] = chash

            f = FileRecord(
                id=fid, snapshot_id=snap1_id, path=f"src/module_{i}.py",
                file_hash=f"hash_{i}", commit_hash="c1", language="python",
                size_bytes=len(content_str), category="source",
                indexed_at="2024-01-01T00:00:00Z", parsing_status="success"
            )
            n = ChunkNode(
                id=cid, file_id=fid, file_path=f.path, chunk_hash=chash,
                start_line=1, end_line=5, byte_range=[0, len(content_str)],
                metadata={"complexity": "high", "semantic_matches": []}
            )
            c = ChunkContent(chunk_hash=chash, content=content_str)
            files.append(f)
            nodes.append(n)
            contents.append(c)

        storage.add_files(files)
        storage.add_contents(contents)
        storage.add_nodes(nodes)

        logger.info("🚀 Running Async Embedder Pipeline...")
        stats_1 = {}
        async for update in embedder.run_indexing(snap1_id, batch_size=200, mock_api=True):
            if update['status'] == 'completed':
                stats_1 = update
            elif update['status'] in ['staging_progress', 'embedding_progress']:
                 print(f"   [Progress] Embedded: {update.get('total_embedded')}", end='\r')
        print("")

        logger.info(f"✅ Result Snap 1: {stats_1}")

        # [FIX] ATTIVIAMO LO SNAPSHOT PER LIBERARE IL LOCK 'indexing'
        storage.activate_snapshot(repo_id, snap1_id, stats=stats_1)
        logger.info("🔓 Snapshot 1 Activated (Status: completed)")

        # ==========================================
        # FASE 2: SNAPSHOT INCREMENTALE
        # ==========================================
        logger.info("\n=== PHASE 2: Incremental Snapshot (Deduplication Test) ===")

        # Ora create_snapshot funzionerà perché snap1 è 'completed'
        snap2_id, created = storage.create_snapshot(repo_id, "commit-incremental", force_new=True)
        if not snap2_id:
            raise RuntimeError("❌ Impossibile creare Snapshot 2: repository lockato o errore unique violation.")

        logger.info(f"📥 Seeding DB for Snap 2 (ID: {snap2_id})...")

        files_2 = []
        nodes_2 = []
        contents_2 = []

        # 1. I vecchi (990)
        for i in range(990):
            fid = str(uuid.uuid4())
            cid = str(uuid.uuid4())
            chash = content_hashes_map[i]

            f = FileRecord(
                id=fid, snapshot_id=snap2_id, path=f"src/module_{i}.py",
                file_hash=f"hash_{i}", commit_hash="c2", language="python",
                size_bytes=100, category="source",
                indexed_at="2024-01-02T00:00:00Z", parsing_status="success"
            )
            n = ChunkNode(
                id=cid, file_id=fid, file_path=f.path, chunk_hash=chash,
                start_line=1, end_line=5, byte_range=[0, 100],
                metadata={"complexity": "high", "semantic_matches": []}
            )
            files_2.append(f)
            nodes_2.append(n)

        # 2. I nuovi (10)
        for i in range(1000, 1010):
            fid = str(uuid.uuid4())
            cid = str(uuid.uuid4())
            content_str = f"def function_NEW_{i}(): return 'brand_new'"
            chash = hashlib.sha256(content_str.encode()).hexdigest()

            f = FileRecord(
                id=fid, snapshot_id=snap2_id, path=f"src/new_module_{i}.py",
                file_hash=f"hash_new_{i}", commit_hash="c2", language="python",
                size_bytes=len(content_str), category="source",
                indexed_at="2024-01-02T00:00:00Z", parsing_status="success"
            )
            n = ChunkNode(
                id=cid, file_id=fid, file_path=f.path, chunk_hash=chash,
                start_line=1, end_line=5, byte_range=[0, len(content_str)],
                metadata={"tag": "new"}
            )
            c = ChunkContent(chunk_hash=chash, content=content_str)
            files_2.append(f)
            nodes_2.append(n)
            contents_2.append(c)

        storage.add_files(files_2)
        storage.add_contents(contents_2)
        storage.add_nodes(nodes_2)

        logger.info("🚀 Running Pipeline for Snapshot 2...")
        stats_2 = {}
        async for update in embedder.run_indexing(snap2_id, batch_size=200, mock_api=True):
             if update['status'] == 'completed':
                stats_2 = update
             elif update['status'] == 'deduplication_stats':
                 logger.info(f"   ♻️  Recovered: {update['recovered']}")

        logger.info(f"✅ Result Snap 2: {stats_2}")

        assert stats_2['total_nodes'] == 1000
        assert stats_2['recovered_from_history'] == 990
        assert stats_2['newly_embedded'] == 10

        logger.info("\n🎉🎉🎉 TEST PASSED: Enterprise Staging Pipeline Works! 🎉🎉🎉")

    except AssertionError as ae:
        logger.error(f"❌ ASSERTION FAILED: {ae}")
        raise ae
    except Exception as e:
        logger.exception("❌ UNEXPECTED ERROR")
        raise e
    finally:
        connector.close()

if __name__ == "__main__":
    asyncio.run(main())
