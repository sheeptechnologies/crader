import json
import logging
import uuid
from typing import Any, Dict, Generator, Iterator, List, Optional, Tuple

import psycopg
from opentelemetry import trace

# from psycopg.rows import dict_row
from .base import GraphStorage
from .connector import DatabaseConnector  # Importiamo l'interfaccia

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


class PostgresGraphStorage(GraphStorage):
    """
    Enterprise-grade Postgres implementation of the GraphStorage interface.

    This class serves as the persistence layer for the Code Property Graph (CPG).
    It leverages PostgreSQL's advanced features including JSONB for flexible metadata,
    pgvector for high-dimensional vector search, and Full-Text Search (FTS) for lexical queries.

    **Key Architectures:**
    *   **Connection Management**: Abducts connection logic via `DatabaseConnector` (Pool vs Single).
    *   **Atomic Snapshots**: Implements ACID-compliant snapshot creation and activation to ensure read consistency.
    *   **Bulk Ingestion**: Uses PostgreSQL `COPY` protocol and `executemany` for high-throughput data insertion.
    *   **Hybrid Search**: Combines `pgvector` (semantic) and `tsvector` (lexical) in optimized SQL queries.

    Attributes:
        connector (DatabaseConnector): The database connection provider.
        vector_dim (int): Dimensionality of the embedding vectors (default 1536 for OpenAI).
    """

    def __init__(self, connector: DatabaseConnector, vector_dim: int = 1536):
        """
        Initializes the storage backend.

        Args:
            connector (DatabaseConnector): The strategy for obtaining DB connections.
            vector_dim (int): The vector size for the embedding model (e.g., 1536 for text-embedding-3-small).
            (to do: vector_dim should depend on the embedding model, so we should have a vector table for every embedding model/provider)
        """
        self.connector = connector
        self.vector_dim = vector_dim

        # We only log that we are ready, not the pool details
        logger.info(f"🐘 PostgresStorage initialized (Vector Dim: {vector_dim})")

    def close(self):
        self.connector.close()

    # ==========================================
    # 1. IDENTITY & LIFECYCLE
    # ==========================================

    def ensure_repository(self, url: str, branch: str, name: str) -> str:
        """
        Registers or updates a repository entry.

        This operation is idempotent. If the repository (url + branch) exists, it updates the name and timestamp.

        Args:
            url (str): The logical URL of the repository (e.g., 'https://github.com/org/repo').
            branch (str): The branch being tracked.
            name (str): A human-readable display name.

        Returns:
            str: The UUID of the repository.
        """
        sql = """
            INSERT INTO repositories (id, url, branch, name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (url, branch) DO UPDATE 
            SET name = EXCLUDED.name, updated_at = NOW()
            RETURNING id
        """
        repo_id = str(uuid.uuid4())
        with self.connector.get_connection() as conn:
            res = conn.execute(sql, (repo_id, url, branch, name)).fetchone()
            return str(res["id"])

    def create_snapshot(
        self, repository_id: str, commit_hash: str, force_new: bool = False
    ) -> Tuple[Optional[str], bool]:
        """
        Initializes a new indexing snapshot.

        This method handles the concurrency control for repo indexing.
        1.  Checks if a valid snapshot already exists for the given commit (unless `force_new` is True).
        2.  If not, creates a new snapshot record with status 'indexing'.
        3.  Uses database constraints to prevent duplicate active indexing jobs for the same commit.

        Args:
            repository_id (str): The target repository ID.
            commit_hash (str): The specific commit SHA.
            force_new (bool): If True, ignores existing completed snapshots.

        Returns:
            Tuple[Optional[str], bool]: A tuple (snapshot_id, is_newly_created).
                                        Returns (None, False) if the repo is locked/busy.
        """
        new_id = str(uuid.uuid4())

        try:
            with self.connector.get_connection() as conn:
                if not force_new:
                    row = conn.execute(
                        """
                        SELECT id, status FROM snapshots 
                        WHERE repository_id = %s AND commit_hash = %s AND status = 'completed'
                        ORDER BY created_at DESC LIMIT 1
                    """,
                        (repository_id, commit_hash),
                    ).fetchone()

                    if row:
                        logger.info(f"✅ Existing snapshot found: {row['id']}")
                        return str(row["id"]), False

                conn.execute(
                    """
                    INSERT INTO snapshots (id, repository_id, commit_hash, status, created_at)
                    VALUES (%s, %s, %s, 'indexing', NOW())
                """,
                    (new_id, repository_id, commit_hash),
                )

                logger.info(f"🔒 Lock acquisito: Inizio indicizzazione snapshot {new_id}")
                return new_id, True

        except psycopg.errors.UniqueViolation:
            logger.info(f"⏳ Repo busy. Setting dirty flag for {repository_id}")
            with self.connector.get_connection() as conn:
                conn.execute(
                    """
                    UPDATE repositories 
                    SET reindex_requested_at = NOW() 
                    WHERE id = %s
                """,
                    (repository_id,),
                )
            return None, False

    def check_and_reset_reindex_flag(self, repository_id: str) -> bool:
        with self.connector.get_connection() as conn:
            row = conn.execute(
                """
                UPDATE repositories 
                SET reindex_requested_at = NULL
                WHERE id = %s AND reindex_requested_at IS NOT NULL
                RETURNING id
            """,
                (repository_id,),
            ).fetchone()
            return row is not None

    def activate_snapshot(
        self, repository_id: str, snapshot_id: str, stats: Dict[str, Any] = None, manifest: Dict[str, Any] = None
    ):
        """
        Promotes a snapshot to 'active' status.

        This atomic transaction:
        1.  Updates the snapshot status to 'completed'.
        2.  Saves the final indexing statistics and file manifest.
        3.  Updates the `repositories.current_snapshot_id` pointer to this new snapshot.

        Args:
            repository_id (str): The repository ID.
            snapshot_id (str): The snapshot to activate.
            stats (Dict): Indexing statistics (node count, parse time, etc.).
            manifest (Dict): The file system structure JSON.
        """
        with self.connector.get_connection() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    UPDATE snapshots 
                    SET status='completed', completed_at=NOW(), stats=%s, file_manifest=%s 
                    WHERE id=%s
                """,
                    (json.dumps(stats or {}), json.dumps(manifest or {}), snapshot_id),
                )

                conn.execute(
                    """
                    UPDATE repositories 
                    SET current_snapshot_id=%s, updated_at=NOW()
                    WHERE id=%s
                """,
                    (snapshot_id, repository_id),
                )
        logger.info(f"🚀 SNAPSHOT ACTIVATED: {snapshot_id}")

    def fail_snapshot(self, snapshot_id: str, error: str):
        with self.connector.get_connection() as conn:
            conn.execute(
                "UPDATE snapshots SET status='failed', stats=jsonb_build_object('error', %s::text) WHERE id=%s",
                (error, snapshot_id),
            )

    def prune_snapshot(self, snapshot_id: str):
        logger.info(f"🧹 Pruning snapshot data: {snapshot_id}")
        with self.connector.get_connection() as conn:
            conn.execute("DELETE FROM files WHERE snapshot_id = %s", (snapshot_id,))

    def get_active_snapshot_id(self, repository_id: str) -> Optional[str]:
        with self.connector.get_connection() as conn:
            row = conn.execute("SELECT current_snapshot_id FROM repositories WHERE id=%s", (repository_id,)).fetchone()
            return str(row["current_snapshot_id"]) if row and row["current_snapshot_id"] else None

    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        with self.connector.get_connection() as conn:
            return conn.execute("SELECT * FROM repositories WHERE id=%s", (repo_id,)).fetchone()

    def get_snapshot_manifest(self, snapshot_id: str) -> Dict[str, Any]:
        sql = "SELECT file_manifest FROM snapshots WHERE id = %s"
        with self.connector.get_connection() as conn:
            row = conn.execute(sql, (snapshot_id,)).fetchone()
            return row["file_manifest"] if row and row["file_manifest"] else {}

    # ==========================================
    # 2. WRITE OPERATIONS (OPTIMIZED)
    # ==========================================

    def add_files(self, files: List[Any]):
        if not files:
            return
        with self.connector.get_connection() as conn:
            with conn.cursor() as cur:
                data = [f.to_dict() for f in files]
                cur.executemany(
                    """
                    INSERT INTO files (id, snapshot_id, commit_hash, file_hash, path, language, size_bytes, category, indexed_at, parsing_status, parsing_error)
                    VALUES (%(id)s, %(snapshot_id)s, %(commit_hash)s, %(file_hash)s, %(path)s, %(language)s, %(size_bytes)s, %(category)s, %(indexed_at)s, %(parsing_status)s, %(parsing_error)s)
                    ON CONFLICT (snapshot_id, path) DO UPDATE 
                    SET file_hash=EXCLUDED.file_hash, parsing_status=EXCLUDED.parsing_status
                """,
                    data,
                )

    def add_nodes(self, nodes: List[Any]):
        """
        Inserts graph nodes with standard conflict handling.

        Use this method when `ON CONFLICT DO NOTHING` is required (e.g., during incremental updates).
        For bulk initial loads, `add_nodes_fast` is preferred.

        Args:
            nodes (List[Any]): List of ChunkNode objects.
        """
        if not nodes:
            return
        with self.connector.get_connection() as conn:
            with conn.cursor() as cur:
                data = []
                for n in nodes:
                    d = n.to_dict()
                    d["metadata"] = json.dumps(d.get("metadata", {}))
                    d["byte_start"] = d["byte_range"][0]
                    d["byte_end"] = d["byte_range"][1]
                    d["size"] = d["byte_end"] - d["byte_start"]
                    data.append(d)
                cur.executemany(
                    """
                    INSERT INTO nodes (id, file_id, file_path, start_line, end_line, byte_start, byte_end, chunk_hash, size, metadata)
                    VALUES (%(id)s, %(file_id)s, %(file_path)s, %(start_line)s, %(end_line)s, %(byte_start)s, %(byte_end)s, %(chunk_hash)s, %(size)s, %(metadata)s)
                    ON CONFLICT (id) DO NOTHING
                """,
                    data,
                )

    def add_nodes_fast(self, nodes: List[Any]):
        """
        Optimized Node Insertion using PostgreSQL `COPY` protocol.

        This method streams data directly into the database table, bypassing the overhead of individual
        INSERT statements. It offers 10x-50x performance improvement for bulk operations.

        **WARNING:** This method does NOT support `ON CONFLICT` clauses. It should only be used
        when the calling context guarantees uniqueness or is initializing a fresh snapshot (e.g. worker processes).

        Args:
            nodes (List[Any]): List of ChunkNode objects using protocol v1.

        Raises:
            Exception: If any raw DB error occurs (e.g. UniqueViolation if data is dirty).
        """
        if not nodes:
            return

        def data_generator():
            for n in nodes:
                d = n.to_dict()
                meta = json.dumps(d.get("metadata", {}))
                bs, be = d["byte_range"]
                # Must respect the column order in the COPY command below
                yield (
                    d["id"],
                    d.get("file_id"),
                    d["file_path"],
                    d["start_line"],
                    d["end_line"],
                    bs,
                    be,
                    d.get("chunk_hash", ""),
                    be - bs,
                    meta,
                )

        sql = """
            COPY nodes (id, file_id, file_path, start_line, end_line, byte_start, byte_end, chunk_hash, size, metadata)
            FROM STDIN
        """

        try:
            with self.connector.get_connection() as conn:
                with conn.cursor() as cur:
                    with cur.copy(sql) as copy:
                        for row in data_generator():
                            copy.write_row(row)
        except Exception as e:
            logger.error(f"❌ COPY failed in add_nodes_fast: {e}")
            raise e

    def add_contents(self, contents: List[Any]):
        if not contents:
            return
        with self.connector.get_connection() as conn:
            with conn.cursor() as cur:
                data = [c.to_dict() for c in contents]
                cur.executemany(
                    "INSERT INTO contents (chunk_hash, content) VALUES (%(chunk_hash)s, %(content)s) ON CONFLICT (chunk_hash) DO NOTHING",
                    data,
                )

    def add_edge(self, source_id, target_id, relation_type, metadata):
        with self.connector.get_connection() as conn:
            conn.execute(
                "INSERT INTO edges (source_id, target_id, relation_type, metadata) VALUES (%s, %s, %s, %s)",
                (source_id, target_id, relation_type, json.dumps(metadata)),
            )

    def save_embeddings(self, vector_documents: List[Dict[str, Any]]):
        if not vector_documents:
            return
        with self.connector.get_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO node_embeddings (
                        id, chunk_id, snapshot_id, vector_hash, model_name, created_at, 
                        file_path, language, category, start_line, end_line, embedding
                    ) VALUES (
                        %(id)s, %(chunk_id)s, %(snapshot_id)s, %(vector_hash)s, %(model_name)s, %(created_at)s,
                        %(file_path)s, %(language)s, %(category)s, %(start_line)s, %(end_line)s, %(embedding)s
                    )
                    ON CONFLICT (id) DO NOTHING
                """,
                    vector_documents,
                )

    def add_search_index(self, search_docs: List[Dict[str, Any]]):
        if not search_docs:
            return
        with self.connector.get_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO nodes_fts (node_id, file_path, semantic_tags, content, search_vector)
                    VALUES (
                        %(node_id)s, %(file_path)s, %(tags)s, %(content)s,
                        setweight(to_tsvector('english', %(tags)s), 'A') || 
                        setweight(to_tsvector('english', %(content)s), 'B')
                    )
                    ON CONFLICT (node_id) DO UPDATE 
                    SET search_vector = EXCLUDED.search_vector, content = EXCLUDED.content
                """,
                    search_docs,
                )

    # ==========================================
    # 3. READ OPERATIONS
    # ==========================================

    def _build_filter_clause(self, filters: Dict[str, Any], col_map: Dict[str, str]) -> Tuple[str, List[Any]]:
        """
        Constructs a dynamic SQL WHERE clause based on abstract filters.

        Translates domain-level filter keys (like 'path_prefix', 'language', 'role') into
        concrete SQL conditions mapping to specific table columns.

        Supported Filters:
        *   `path_prefix`: Matches file paths starting with the given string(s).
        *   `language`: Exact match on file language.
        *   `role`: JSON search within the `metadata` column for semantic roles (e.g., 'class', 'function').
        *   `category`: Filtering by file category (e.g., 'test', 'source').
        *   `exclude_*`: Negated versions of the above.

        Args:
            filters (Dict[str, Any]): The filter criteria dictionary.
            col_map (Dict[str, str]): Mapping from abstract keys ('path', 'lang', 'meta', 'cat') to actual Table.Column names.

        Returns:
            Tuple[str, List[Any]]: A tuple containing the SQL string (starting with " AND ...") and the list of parameters.
        """
        if not filters:
            return "", []
        clauses = []
        params = []

        def as_list(val):
            return val if isinstance(val, list) else [val]

        if filters.get("path_prefix"):
            col = col_map.get("path")
            if col:
                paths = as_list(filters["path_prefix"])
                if paths:
                    or_clauses = []
                    for p in paths:
                        or_clauses.append(f"{col} LIKE %s")
                        params.append(p.rstrip("/") + "%")
                    clauses.append(f"({' OR '.join(or_clauses)})")

        if filters.get("language"):
            col = col_map.get("lang")
            if col:
                langs = as_list(filters["language"])
                if langs:
                    clauses.append(f"{col} = ANY(%s)")
                    params.append(langs)

        if filters.get("exclude_language"):
            col = col_map.get("lang")
            if col:
                ex_langs = as_list(filters["exclude_language"])
                if ex_langs:
                    clauses.append(f"{col} != ALL(%s)")
                    params.append(ex_langs)

        col_meta = col_map.get("meta")
        if col_meta:
            if filters.get("role"):
                roles = as_list(filters["role"])
                role_clauses = []
                for r in roles:
                    role_clauses.append(f"{col_meta} @> %s::jsonb")
                    params.append(json.dumps({"semantic_matches": [{"category": "role", "value": r}]}))
                if role_clauses:
                    clauses.append(f"({' OR '.join(role_clauses)})")

            if filters.get("exclude_role"):
                ex_roles = as_list(filters["exclude_role"])
                ex_clauses = []
                for r in ex_roles:
                    ex_clauses.append(f"{col_meta} @> %s::jsonb")
                    params.append(json.dumps({"semantic_matches": [{"category": "role", "value": r}]}))
                if ex_clauses:
                    clauses.append(f"NOT ({' OR '.join(ex_clauses)})")

        col_cat = col_map.get("cat")
        if col_cat:
            if filters.get("category"):
                cats = as_list(filters["category"])
                clauses.append(f"{col_cat} = ANY(%s)")
                params.append(cats)

            if filters.get("exclude_category"):
                ex_cats = as_list(filters["exclude_category"])
                clauses.append(f"{col_cat} != ALL(%s)")
                params.append(ex_cats)

        if not clauses:
            return "", []
        return " AND " + " AND ".join(clauses), params

    def search_vectors(
        self, query_vector: List[float], limit: int, snapshot_id: str, filters: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        """
        Executes a Semantic Vector Search (ANN).

        Uses `pgvector` to find nodes with embeddings closest to the `query_vector`.
        Applies strict filtering via `snapshot_id` to ensure consistency.

        Args:
            query_vector (List[float]): The 1536-d embedding vector.
            limit (int): Max results.
            snapshot_id (str): The context snapshot.
            filters (Dict[str, Any]): Additional metadata filters.

        Returns:
            List[Dict[str, Any]]: Search results containing node content, similarity score (1 - cosine_dist), and metadata.
        """

        if not snapshot_id:
            raise ValueError("snapshot_id mandatory.")

        sql = """
            SELECT ne.chunk_id, ne.file_path, ne.start_line, ne.end_line, ne.snapshot_id, n.metadata, c.content, ne.language, 
                (ne.embedding <=> %s::vector) as distance
            FROM node_embeddings ne 
            JOIN nodes n ON ne.chunk_id = n.id 
            JOIN contents c ON n.chunk_hash = c.chunk_hash
            WHERE ne.snapshot_id = %s
        """
        params = [query_vector, snapshot_id]
        col_map = {"path": "ne.file_path", "lang": "ne.language", "cat": "ne.category", "meta": "n.metadata"}

        filter_sql, filter_params = self._build_filter_clause(filters, col_map)
        sql += filter_sql
        params.extend(filter_params)

        sql += " ORDER BY distance ASC LIMIT %s"
        params.append(limit)

        with tracer.start_as_current_span("db.search.vectors") as span:
            span.set_attribute("search.limit", limit)
            span.set_attribute("snapshot.id", snapshot_id)
            if filters:
                span.set_attribute("search.filters_keys", list(filters.keys()))

            with self.connector.get_connection() as conn:
                results = []
                # Here we implicitly measure query execution time as well
                for row in conn.execute(sql, params).fetchall():
                    results.append(
                        {
                            "id": str(row["chunk_id"]),
                            "file_path": row["file_path"],
                            "start_line": row["start_line"],
                            "end_line": row["end_line"],
                            "snapshot_id": str(row["snapshot_id"]),
                            "metadata": row["metadata"],
                            "content": row["content"],
                            "language": row["language"],
                            "score": 1 - row["distance"],
                        }
                    )

                span.set_attribute("search.results_count", len(results))
                return results

    def search_fts(
        self, query: str, limit: int, snapshot_id: str, filters: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        """
        Executes a Full-Text Search (Lexical).

        Uses PostgreSQL's built-in `websearch_to_tsquery` to support natural language queries with operators
        (e.g., "foo or bar", "-baz") against a pre-computed `tsvector` index.

        Args:
            query (str): The text query.
            limit (int): Max results.
            snapshot_id (str): The context snapshot.
            filters (Dict[str, Any]): Additional metadata filters.

        Returns:
            List[Dict[str, Any]]: Ranked results containing snippets and metadata.
        """
        if not snapshot_id:
            raise ValueError("snapshot_id mandatory.")
        sql = """
            SELECT fts.node_id, fts.file_path, n.start_line, n.end_line, fts.content, f.snapshot_id, n.metadata, f.language,
                   ts_rank(fts.search_vector, websearch_to_tsquery('english', %s)) as rank
            FROM nodes_fts fts 
            JOIN nodes n ON fts.node_id = n.id 
            JOIN files f ON n.file_id = f.id
            WHERE fts.search_vector @@ websearch_to_tsquery('english', %s) 
            AND f.snapshot_id = %s
        """
        params = [query, query, snapshot_id]
        col_map = {"path": "f.path", "lang": "f.language", "cat": "f.category", "meta": "n.metadata"}

        filter_sql, filter_params = self._build_filter_clause(filters, col_map)
        sql += filter_sql
        params.extend(filter_params)

        sql += " ORDER BY rank DESC LIMIT %s"
        params.append(limit)

        try:
            with self.connector.get_connection() as conn:
                results = []
                for row in conn.execute(sql, params).fetchall():
                    results.append(
                        {
                            "id": str(row["node_id"]),
                            "file_path": row["file_path"],
                            "start_line": row["start_line"],
                            "end_line": row["end_line"],
                            "score": row["rank"],
                            "content": row["content"],
                            "snapshot_id": str(row["snapshot_id"]),
                            "metadata": row["metadata"],
                            "language": row["language"],
                        }
                    )
                return results
        except Exception as e:
            logger.error(f"Postgres FTS Error: {e}")
            return []

    # ==========================================
    # 4. UTILS & NAVIGATION
    # ==========================================

    def find_chunk_id(self, file_path: str, byte_range: List[int], snapshot_id: str) -> Optional[str]:
        """
        Locates the specific ChunkNode covering a given byte range in a file.

        This is crucial for mapping external tool output (e.g., LSPs, linters) which typically provide
        byte offsets, back to the internal Node IDs used by the graph system.

        Args:
            file_path (str): Relative path of the file.
            byte_range (List[int]): A [start_byte, end_byte] pair.
            snapshot_id (str): The context snapshot.

        Returns:
            Optional[str]: The UUID of the most specific enclosing node, or None if not found.
        """
        if not byte_range or not snapshot_id:
            return None
        sql = """
            SELECT n.id FROM nodes n JOIN files f ON n.file_id = f.id 
            WHERE f.path = %s AND f.snapshot_id = %s
              AND n.byte_start <= %s + 1 AND n.byte_end >= %s - 1
            ORDER BY n.size ASC LIMIT 1
        """
        with self.connector.get_connection() as conn:
            row = conn.execute(sql, (file_path, snapshot_id, byte_range[0], byte_range[1])).fetchone()
            return str(row["id"]) if row else None

    def get_incoming_references(self, target_node_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self.connector.get_connection() as conn:
            res = []
            for r in conn.execute(
                "SELECT s.id, s.file_path, s.start_line, e.relation_type, e.metadata FROM edges e JOIN nodes s ON e.source_id=s.id WHERE e.target_id=%s AND e.relation_type IN ('calls', 'references', 'imports', 'instantiates') ORDER BY s.file_path, s.start_line LIMIT %s",
                (target_node_id, limit),
            ).fetchall():
                res.append(
                    {
                        "source_id": str(r["id"]),
                        "file": r["file_path"],
                        "line": r["start_line"],
                        "relation": r["relation_type"],
                        "context_snippet": r["metadata"].get("description", ""),
                    }
                )
            return res

    def get_outgoing_calls(self, source_node_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self.connector.get_connection() as conn:
            res = []
            for r in conn.execute(
                "SELECT t.id, t.file_path, t.start_line, e.relation_type, e.metadata FROM edges e JOIN nodes t ON e.target_id=t.id WHERE e.source_id=%s AND e.relation_type IN ('calls', 'instantiates', 'imports') ORDER BY t.file_path, t.start_line LIMIT %s",
                (source_node_id, limit),
            ).fetchall():
                res.append(
                    {
                        "target_id": str(r["id"]),
                        "file": r["file_path"],
                        "line": r["start_line"],
                        "relation": r["relation_type"],
                        "symbol": r["metadata"].get("symbol", ""),
                    }
                )
            return res

    def get_context_neighbors(self, node_id: str):
        res = {"parents": [], "calls": []}
        with self.connector.get_connection() as conn:
            for r in conn.execute(
                "SELECT t.id, t.file_path, t.start_line, e.metadata, t.metadata FROM edges e JOIN nodes t ON e.target_id=t.id WHERE e.source_id=%s AND e.relation_type='child_of'",
                (node_id,),
            ).fetchall():
                res["parents"].append(
                    {
                        "id": str(r["id"]),
                        "file_path": r["file_path"],
                        "start_line": r["start_line"],
                        "edge_meta": r["metadata"],
                        "metadata": r["metadata"],
                    }
                )
            for r in conn.execute(
                "SELECT t.id, t.file_path, e.metadata FROM edges e JOIN nodes t ON e.target_id=t.id WHERE e.source_id=%s AND e.relation_type IN ('calls','references') LIMIT 15",
                (node_id,),
            ).fetchall():
                res["calls"].append({"id": str(r["id"]), "symbol": r["metadata"].get("symbol", "unknown")})
        return res

    def get_neighbor_chunk(self, node_id: str, direction: str = "next") -> Optional[Dict[str, Any]]:
        with self.connector.get_connection() as conn:
            curr = conn.execute("SELECT file_id, start_line, end_line FROM nodes WHERE id=%s", (node_id,)).fetchone()
            if not curr:
                return None
            fid, s, e = curr["file_id"], curr["start_line"], curr["end_line"]

            if direction == "next":
                sql = "SELECT n.id, n.start_line, n.end_line, n.chunk_hash, c.content, n.metadata, n.file_path FROM nodes n JOIN contents c ON n.chunk_hash=c.chunk_hash WHERE n.file_id=%s AND n.start_line >= %s AND n.id!=%s ORDER BY n.start_line ASC LIMIT 1"
                p = (fid, e, node_id)
            else:
                sql = "SELECT n.id, n.start_line, n.end_line, n.chunk_hash, c.content, n.metadata, n.file_path FROM nodes n JOIN contents c ON n.chunk_hash=c.chunk_hash WHERE n.file_id=%s AND n.end_line <= %s AND n.id!=%s ORDER BY n.end_line DESC LIMIT 1"
                p = (fid, s, node_id)
            row = conn.execute(sql, p).fetchone()
            if row:
                return {
                    "id": str(row["id"]),
                    "start_line": row["start_line"],
                    "end_line": row["end_line"],
                    "chunk_hash": row["chunk_hash"],
                    "content": row["content"],
                    "metadata": row["metadata"],
                    "file_path": row["file_path"],
                }
            return None

    def get_neighbor_metadata(self, node_id: str) -> Dict[str, Any]:
        info = {"next": None, "prev": None, "parent": None}
        with self.connector.get_connection() as conn:
            curr = conn.execute("SELECT file_id, start_line, end_line FROM nodes WHERE id=%s", (node_id,)).fetchone()
            if not curr:
                return info
            fid, s, e = curr["file_id"], curr["start_line"], curr["end_line"]
            rn = conn.execute(
                "SELECT id, metadata FROM nodes WHERE file_id=%s AND start_line >= %s AND id!=%s ORDER BY start_line ASC LIMIT 1",
                (fid, e, node_id),
            ).fetchone()
            if rn:
                info["next"] = self._format_nav_node(rn)
            rp = conn.execute(
                "SELECT id, metadata FROM nodes WHERE file_id=%s AND end_line <= %s AND id!=%s ORDER BY end_line DESC LIMIT 1",
                (fid, s, node_id),
            ).fetchone()
            if rp:
                info["prev"] = self._format_nav_node(rp)
            rpar = conn.execute(
                "SELECT t.id, t.metadata FROM edges e JOIN nodes t ON e.target_id=t.id WHERE e.source_id=%s AND e.relation_type='child_of' LIMIT 1",
                (node_id,),
            ).fetchone()
            if rpar:
                info["parent"] = self._format_nav_node(rpar)
        return info

    def _format_nav_node(self, row):
        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        matches = meta.get("semantic_matches", [])
        label = "Code Block"
        for m in matches:
            if m.get("category") == "role":
                label = m.get("label") or m.get("value")
                break
            if m.get("category") == "type":
                label = m.get("label") or m.get("value")
        return {"id": str(row["id"]), "label": label}

    def get_vectors_by_hashes(self, vector_hashes: List[str], model_name: str) -> Dict[str, List[float]]:
        if not vector_hashes:
            return {}
        res = {}
        with self.connector.get_connection() as conn:
            query = "SELECT DISTINCT ON (vector_hash) vector_hash, embedding FROM node_embeddings WHERE vector_hash = ANY(%s) AND model_name = %s"
            for r in conn.execute(query, (vector_hashes, model_name)).fetchall():
                if r["embedding"] is not None:
                    res[r["vector_hash"]] = r["embedding"]
        return res

    def get_incoming_definitions_bulk(self, node_ids: List[str]) -> Dict[str, List[str]]:
        if not node_ids:
            return {}
        res = {}
        with self.connector.get_connection() as conn:
            for i in range(0, len(node_ids), 500):
                batch = node_ids[i : i + 500]
                for r in conn.execute(
                    "SELECT target_id, metadata FROM edges WHERE target_id = ANY(%s) AND relation_type='calls'",
                    (batch,),
                ).fetchall():
                    sym = r["metadata"].get("symbol")
                    if sym:
                        tid = str(r["target_id"])
                        if tid not in res:
                            res[tid] = set()
                        res[tid].add(sym)
        return {k: list(v) for k, v in res.items()}

    def get_contents_bulk(self, chunk_hashes: List[str]) -> Dict[str, str]:
        if not chunk_hashes:
            return {}
        res = {}
        with self.connector.get_connection() as conn:
            for i in range(0, len(chunk_hashes), 500):
                batch = chunk_hashes[i : i + 500]
                for r in conn.execute(
                    "SELECT chunk_hash, content FROM contents WHERE chunk_hash = ANY(%s)", (batch,)
                ).fetchall():
                    res[r["chunk_hash"]] = r["content"]
        return res

    def list_file_paths(self, snapshot_id: str) -> List[str]:
        sql = "SELECT path FROM files WHERE snapshot_id = %s ORDER BY path"
        with self.connector.get_connection() as conn:
            return [r["path"] for r in conn.execute(sql, (snapshot_id,)).fetchall()]

    def get_file_content_range(
        self, snapshot_id: str, file_path: str, start_line: int = None, end_line: int = None
    ) -> Optional[str]:
        sl = start_line if start_line is not None else 0
        el = end_line if end_line is not None else 999999
        sql = """
            SELECT c.content, n.start_line
            FROM nodes n JOIN files f ON n.file_id = f.id JOIN contents c ON n.chunk_hash = c.chunk_hash
            WHERE f.snapshot_id = %s AND f.path = %s AND n.end_line >= %s AND n.start_line <= %s
            ORDER BY n.byte_start ASC
        """
        with self.connector.get_connection() as conn:
            rows = conn.execute(sql, (snapshot_id, file_path, sl, el)).fetchall()
        if not rows:
            with self.connector.get_connection() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM files WHERE snapshot_id=%s AND path=%s", (snapshot_id, file_path)
                ).fetchone()
            if exists:
                return ""
            return None
        full_blob = "".join([r["content"] for r in rows])
        first_chunk_start = rows[0]["start_line"]
        lines = full_blob.splitlines(keepends=True)
        rel_start = max(0, sl - first_chunk_start) if start_line else 0
        if end_line:
            rel_end = min(len(lines), el - first_chunk_start + 1)
        else:
            rel_end = len(lines)
        return "".join(lines[rel_start:rel_end])

    def get_stats(self):
        with self.connector.get_connection() as conn:
            return {
                "files": conn.execute("SELECT COUNT(*) as c FROM files").fetchone()["c"],
                "total_nodes": conn.execute("SELECT COUNT(*) as c FROM nodes").fetchone()["c"],
                "embeddings": conn.execute("SELECT COUNT(*) as c FROM node_embeddings").fetchone()["c"],
                "snapshots": conn.execute("SELECT COUNT(*) as c FROM snapshots").fetchone()["c"],
                "repos": conn.execute("SELECT COUNT(*) as c FROM repositories").fetchone()["c"],
            }

    # ==========================================
    # 2. WRITE OPERATIONS (RAW TUPLES & COPY)
    # ==========================================

    def add_files_raw(self, files_tuples: List[Tuple]):
        """Massive files insertion."""
        if not files_tuples:
            return
        with self.connector.get_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO files (id, snapshot_id, commit_hash, file_hash, path, language, size_bytes, category, indexed_at, parsing_status, parsing_error)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (snapshot_id, path) DO UPDATE 
                    SET file_hash=EXCLUDED.file_hash, parsing_status=EXCLUDED.parsing_status
                """,
                    files_tuples,
                )

    def add_nodes_raw(self, nodes_tuples: List[Tuple]):
        """Massive nodes insertion via COPY (Extremely fast)."""
        if not nodes_tuples:
            return
        sql = """
            COPY nodes (id, file_id, file_path, start_line, end_line, byte_start, byte_end, chunk_hash, size, metadata)
            FROM STDIN
        """
        with tracer.start_as_current_span("db.write.nodes_copy") as span:
            batch_size = len(nodes_tuples)
            span.set_attribute("db.batch_size", batch_size)
            span.set_attribute("db.table", "nodes")

            try:
                with self.connector.get_connection() as conn:
                    with conn.cursor() as cur:
                        with cur.copy(sql) as copy:
                            for row in nodes_tuples:
                                copy.write_row(row)

            except Exception as e:
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR))
                logger.error(f"❌ COPY failed in add_nodes_raw: {e}")
                raise e

    def add_contents_raw(self, contents_tuples: List[Tuple]):
        """Massive contents insertion."""
        if not contents_tuples:
            return
        with self.connector.get_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO contents (chunk_hash, content) VALUES (%s, %s) ON CONFLICT (chunk_hash) DO NOTHING",
                    contents_tuples,
                )

    def add_relations_raw(self, rels_tuples: List[Tuple]):
        """Massive relations insertion."""
        if not rels_tuples:
            return
        with self.connector.get_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO edges (source_id, target_id, relation_type, metadata) VALUES (%s, %s, %s, %s)",
                    rels_tuples,
                )

    def ingest_scip_relations(self, relations_tuples: List[Tuple], snapshot_id: str):
        """
        High-performance ingestion of SCIP relations.

        Resolves node IDs directly in the DB using spatial JOINs on byte ranges.
        Automatically finds the 'most specific' (smallest) node containing the relation range.

        Args:
            relations_tuples: List of tuples in format:
            (source_path, s_start, s_end, target_path, t_start, t_end, rel_type, meta_json)
            snapshot_id: Current snapshot ID to limit joins.
        """
        if not relations_tuples:
            return

        # We use ON COMMIT DROP: the table lives only as long as the transaction is open.
        ddl_temp = """
            CREATE TEMP TABLE IF NOT EXISTS temp_scip_staging (
                s_path TEXT, s_start INT, s_end INT,
                t_path TEXT, t_start INT, t_end INT,
                rel_type TEXT, meta JSONB
            ) ON COMMIT DROP; 
        """

        sql_resolve = """
            INSERT INTO edges (source_id, target_id, relation_type, metadata)
            SELECT DISTINCT ON (t.s_path, t.s_start, t.t_path, t.t_start, t.rel_type)
                ns.id, nt.id, t.rel_type, t.meta
            FROM temp_scip_staging t
            JOIN files fs ON fs.snapshot_id = %s AND fs.path = t.s_path
            JOIN nodes ns ON ns.file_id = fs.id 
                AND ns.byte_start <= t.s_start AND ns.byte_end >= t.s_end
            JOIN files ft ON ft.snapshot_id = %s AND ft.path = t.t_path
            JOIN nodes nt ON nt.file_id = ft.id 
                AND nt.byte_start <= t.t_start AND nt.byte_end >= t.t_end
            
            WHERE ns.id != nt.id -- <--- STOP SELF LOOPS
            
            ORDER BY t.s_path, t.s_start, t.t_path, t.t_start, t.rel_type, 
                     ns.size ASC, nt.size ASC
        """

        # CRITICAL: We use conn.transaction()
        # This temporarily disables autocommit for this block.
        # Ensures the TEMP table survives between CREATE and COPY,
        # and is automatically deleted (ON COMMIT DROP) when exiting the block.
        with tracer.start_as_current_span("db.scip.ingest_transaction") as span:
            span.set_attribute("db.batch_size", len(relations_tuples))
            span.set_attribute("snapshot.id", snapshot_id)

            try:
                with self.connector.get_connection() as conn:
                    with conn.transaction():
                        with conn.cursor() as cur:
                            cur.execute(ddl_temp)

                            # [OTEL] Phase 1: Raw Data Loading (I/O Bound)
                            with tracer.start_as_current_span("db.scip.copy_temp") as copy_span:
                                copy_span.set_attribute("row.count", len(relations_tuples))
                                with cur.copy("COPY temp_scip_staging FROM STDIN") as copy:
                                    for row in relations_tuples:
                                        copy.write_row(row)

                            # [OTEL] Phase 2: Relational Resolution (CPU/Join Bound)
                            with tracer.start_as_current_span("db.scip.resolve_query") as resolve_span:
                                cur.execute(sql_resolve, (snapshot_id, snapshot_id))
                                resolve_span.set_attribute("edges.created", cur.rowcount)

                            logger.info(f"🔗 SCIP Bulk Ingestion: {cur.rowcount} edges created.")

            except Exception as e:
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR))
                logger.error(f"❌ SCIP Ingestion Failed: {e}")
                raise e

    # ==========================================
    # 3. EMBEDDING OPERATIONS
    # ==========================================

    def prepare_embedding_staging(self):
        """
        Initializes the ephemeral staging table for vector computation.

        This buffer table `staging_embeddings` allows us to parallelize embedding generation
        without locking the main `node_embeddings` table. It also serves as the workspace
        for deduplication logic.

        The table is UNLOGGED for performance, as durability of staging data is not required.
        """
        sql_drop = "DROP TABLE IF EXISTS staging_embeddings"

        sql_create = """
            CREATE UNLOGGED TABLE IF NOT EXISTS staging_embeddings (
                id TEXT PRIMARY KEY,        -- FIX: TEXT (not UUID) for compatibility with node_embeddings.id
                chunk_id TEXT NOT NULL,     -- FIX: TEXT (not UUID) for compatibility with nodes.id
                snapshot_id TEXT NOT NULL,
                vector_hash TEXT NOT NULL,
                embedding VECTOR(1536),
                file_path TEXT,
                language TEXT,
                category TEXT,
                start_line INTEGER,
                end_line INTEGER,
                model_name TEXT,
                content TEXT
            );
        """
        with self.connector.get_connection() as conn:
            # We drop the old table to ensure the new one has the correct types (TEXT)
            conn.execute(sql_drop)
            conn.execute(sql_create)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_staging_snap_vhash ON staging_embeddings(snapshot_id, vector_hash)"
            )

    def load_staging_data(self, data_generator: Iterator[Tuple]):
        """
        Loading via COPY.
        """
        sql = """
            COPY staging_embeddings (id, chunk_id, snapshot_id, vector_hash, file_path, language, category, start_line, end_line, model_name, content)
            FROM STDIN
        """
        with tracer.start_as_current_span("db.staging.load") as span:
            try:
                with self.connector.get_connection() as conn:
                    with conn.cursor() as cur:
                        with cur.copy(sql) as copy:
                            count = 0
                            for row in data_generator:
                                copy.write_row(row)
                                count += 1
                            span.set_attribute("rows_loaded", count)
            except Exception as e:
                logger.error(f"Copy to staging failed: {e}")
                raise e

    def backfill_staging_vectors(self, snapshot_id: str) -> int:
        """
        Performs vector deduplication against historical data.

        This method queries the `node_embeddings` table to find if any content in the current
        staging buffer has previously been embedded. If a match is found based on `vector_hash`
        (a deterministic hash of content + model), the existing embedding vector is copied over.

        This dramatically reduces API costs by avoiding re-embedding unchanged code.

        Args:
            snapshot_id (str): The current snapshot ID in staging.

        Returns:
            int: The number of vectors successfully recovered from cache.
        """
        sql = """
            WITH historic_vectors AS (
                SELECT DISTINCT ON (vector_hash) vector_hash, embedding
                FROM node_embeddings
                WHERE vector_hash IN (SELECT vector_hash FROM staging_embeddings WHERE snapshot_id = %s)
                AND embedding IS NOT NULL
            )
            UPDATE staging_embeddings s
            SET embedding = h.embedding
            FROM historic_vectors h
            WHERE s.vector_hash = h.vector_hash
            AND s.snapshot_id = %s
        """
        with tracer.start_as_current_span("db.staging.backfill") as span:
            with self.connector.get_connection() as conn:
                res = conn.execute(sql, (snapshot_id, snapshot_id))
                count = res.rowcount
                logger.info(f"♻️  Deduplicated {count} vectors from history.")
                return count

    def flush_staged_hits(self, snapshot_id: str) -> int:
        """
        Promotes fully calculated embeddings from Staging to Production.

        Moves all rows from `staging_embeddings` that have a valid `embedding` (either newly calculated or backfilled)
        into the main `node_embeddings` table. Afterwards, it removes them from staging.

        Args:
            snapshot_id (str): The active snapshot ID.

        Returns:
            int: Number of rows promoted.
        """
        sql = """
            WITH moved_rows AS (
                INSERT INTO node_embeddings (
                    id, chunk_id, snapshot_id, vector_hash, model_name, created_at, embedding,
                    file_path, language, category, start_line, end_line
                )
                SELECT 
                    id, chunk_id, %s, vector_hash, model_name, NOW(), embedding,
                    file_path, language, category, start_line, end_line
                FROM staging_embeddings
                WHERE embedding IS NOT NULL 
                AND snapshot_id = %s
                RETURNING id
            )
            DELETE FROM staging_embeddings 
            WHERE id IN (SELECT id FROM moved_rows)
        """
        with tracer.start_as_current_span("db.staging.flush_hits"):
            with self.connector.get_connection() as conn:
                res = conn.execute(sql, (snapshot_id, snapshot_id))
                return res.rowcount

    def fetch_staging_delta(self, snapshot_id: str, batch_size: int = 2000) -> Generator[List[Dict], None, None]:
        """
        Fetch Delta.
        """
        sql = """
            SELECT id, content, model_name, file_path, language, category, start_line, end_line, chunk_id, vector_hash
            FROM staging_embeddings
            WHERE snapshot_id = %s
        """
        cursor_name = f"delta_stream_{uuid.uuid4().hex}"

        with self.connector.get_connection() as conn:
            with conn.transaction():
                with conn.cursor(name=cursor_name) as cur:
                    cur.itersize = batch_size
                    cur.execute(sql, (snapshot_id,))
                    while True:
                        rows = cur.fetchmany(batch_size)
                        if not rows:
                            break
                        yield rows

    def cleanup_staging(self, snapshot_id: str):
        """
        Final cleanup of staging data for this snapshot.
        """
        sql = "DELETE FROM staging_embeddings WHERE snapshot_id = %s"
        with self.connector.get_connection() as conn:
            conn.execute(sql, (snapshot_id,))

    def save_embeddings_direct(self, records: List[Dict[str, Any]]):
        """
        Direct writing.
        """
        if not records:
            return
        sql = """
            INSERT INTO node_embeddings (
                id, chunk_id, snapshot_id, vector_hash, model_name, created_at, 
                file_path, language, category, start_line, end_line, embedding
            ) VALUES (
                %(id)s, %(chunk_id)s, %(snapshot_id)s, %(vector_hash)s, %(model_name)s, %(created_at)s,
                %(file_path)s, %(language)s, %(category)s, %(start_line)s, %(end_line)s, %(embedding)s
            )
            ON CONFLICT (id) DO NOTHING
        """
        with self.connector.get_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, records)

    # ==========================================
    # SUPER QUERY (Updated)
    # ==========================================

    def get_nodes_to_embed(self, snapshot_id: str, model_name: str, batch_size: int = 2000):
        sql = """
            SELECT 
                n.id, 
                n.file_path, 
                n.chunk_hash, 
                n.start_line, 
                n.end_line, 
                n.metadata,
                f.language, 
                f.category,
                c.content,
                COALESCE(
                    (
                        SELECT array_agg(DISTINCT e.metadata->>'symbol') 
                        FROM edges e 
                        WHERE e.target_id = n.id 
                          AND e.relation_type = 'calls'
                    ), 
                    '{}'
                ) as incoming_definitions
            FROM files f 
            JOIN nodes n ON f.id = n.file_id
            JOIN contents c ON n.chunk_hash = c.chunk_hash
            LEFT JOIN node_embeddings ne ON (n.id = ne.chunk_id AND ne.model_name = %s)
            WHERE f.snapshot_id = %s 
              AND ne.id IS NULL
        """

        cursor_name = f"embed_stream_{uuid.uuid4().hex}"

        with self.connector.get_connection() as conn:
            with conn.transaction():
                with conn.cursor(name=cursor_name) as cur:
                    cur.itersize = batch_size
                    cur.execute(sql, (model_name, snapshot_id))

                    for r in cur:
                        yield {
                            "id": str(r["id"]),
                            "file_path": r["file_path"],
                            "chunk_hash": r["chunk_hash"],
                            "start_line": r["start_line"],
                            "end_line": r["end_line"],
                            "metadata_json": json.dumps(r["metadata"]),
                            "snapshot_id": snapshot_id,
                            "language": r["language"],
                            "category": r["category"],
                            "content": r["content"],
                            "incoming_definitions": r["incoming_definitions"] if r["incoming_definitions"] else [],
                        }
