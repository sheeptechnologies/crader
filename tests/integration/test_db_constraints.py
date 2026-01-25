import os
import pytest
import sqlalchemy
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, InternalError
from alembic.config import Config
from alembic import command
from pathlib import Path
import time
import json
import concurrent.futures

# Paths
ROOT_DIR = Path(__file__).parent.parent.parent
ALEMBIC_DIR = ROOT_DIR / "src" / "crader" / "db"
ALEMBIC_INI_PATH = ALEMBIC_DIR / "alembic.ini"

# Database Configuration
# Uses default PostgreSQL credentials unless CRADER_TEST_DB_URL is set in environment.
DEFAULT_DB_URL = "postgresql://sheep_user:sheep_password@localhost:5433/sheep_index"
TEST_DB_URL = os.getenv("CRADER_TEST_DB_URL", DEFAULT_DB_URL)

@pytest.fixture(scope="session")
def db_engine():
    """Create a global SQLAlchemy engine for the test session with connection pooling."""
    engine = create_engine(TEST_DB_URL, isolation_level="AUTOCOMMIT", pool_size=5, max_overflow=10)
    yield engine
    engine.dispose()

@pytest.fixture(scope="module")
def migrated_db(db_engine):
    """
    Module-level fixture that ensures the database is clean and fully migrated before running tests.
    It drops the public schema and re-runs Alembic migrations to 'head'.
    """
    # 1. Drop public schema to clean everything (cascades to all tables/functions)
    with db_engine.connect() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))

    # 2. Configure and run Alembic Upgrade
    alembic_cfg = Config(str(ALEMBIC_INI_PATH))
    alembic_cfg.set_main_option("sqlalchemy.url", TEST_DB_URL)
    alembic_cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    
    command.upgrade(alembic_cfg, "head")
    yield

@pytest.fixture(scope="function")
def db_session(db_engine, migrated_db):
    """
    Function-level fixture yielding a connection that rolls back after each test.
    This ensures test isolation so that changes in one test do not affect others.
    """
    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()

# Helper to generate a dummy embedding vector of size 1536
def vec(val=0.0):
    return str([float(val)] * 1536)

# =================================================================
# 1) Extensions and Database Defaults
# =================================================================

def test_extensions_installed(db_session):
    """Verifies that pgcrypto, vector, btree_gist, and pg_trgm extensions are correctly installed."""
    result = db_session.execute(text("SELECT extname FROM pg_extension")).fetchall()
    installed = {row[0] for row in result}
    expected = {'pgcrypto', 'vector', 'btree_gist', 'pg_trgm'}
    # Ensure all expected extensions are present in the installed set
    assert expected.issubset(installed), f"Missing extensions: {expected - installed}"

def test_uuid_defaults(db_session):
    """Verifies that the database automatically generates UUIDs using gen_random_uuid()."""
    # Insert keys without specifying 'id'
    db_session.execute(text("INSERT INTO repositories (name, url) VALUES ('test_repo', 'u')"))
    # Retrieve the row and check if 'id' was populated
    row = db_session.execute(text("SELECT id FROM repositories WHERE name='test_repo'")).fetchone()
    assert row[0] is not None

def test_jsonb_defaults(db_session):
    """Verifies that the default value for metadata columns is an empty JSONB object ('{}')."""
    db_session.execute(text("INSERT INTO repositories (name, url) VALUES ('test_meta', 'u')"))
    meta = db_session.execute(text("SELECT metadata FROM repositories WHERE name='test_meta'")).scalar()
    # Should be an empty dictionary, not None or string
    assert meta == {} and isinstance(meta, dict)

def test_array_defaults(db_session):
    """Verifies that the default value for scope_path is an empty array ('{}'::text[])."""
    # Setup hierarchy
    repo_id = db_session.execute(text("INSERT INTO repositories (name, url) VALUES ('r', 'u') RETURNING id")).scalar()
    snap_id = db_session.execute(text(f"INSERT INTO snapshots (repository_id, commit_hash) VALUES ('{repo_id}', 'c') RETURNING id")).scalar()
    file_id = db_session.execute(text(f"INSERT INTO files (snapshot_id, path, language, size_bytes) VALUES ('{snap_id}', 'f', 'py', 10) RETURNING id")).scalar()
    
    # Insert symbol without specifying scope_path
    db_session.execute(text(f"""
        INSERT INTO symbols (file_id, snapshot_id, kind, name, byte_range)
        VALUES ('{file_id}', '{snap_id}', 'function', 'foo', '[0, 10)')
    """))
    scope_path = db_session.execute(text("SELECT scope_path FROM symbols WHERE name='foo'")).scalar()
    # Expect empty list
    assert scope_path == [] and isinstance(scope_path, list)

# =================================================================
# 2) Uniqueness and Referential Integrity Constraints
# =================================================================

def create_hierarchy(db_session, suffix=''):
    """Helper to create a basic repository and snapshot hierarchy."""
    repo_id = db_session.execute(text(f"INSERT INTO repositories (name, url) VALUES ('r{suffix}', 'u') RETURNING id")).scalar()
    snap_id = db_session.execute(text(f"INSERT INTO snapshots (repository_id, commit_hash) VALUES ('{repo_id}', 'h') RETURNING id")).scalar()
    return repo_id, snap_id

def test_snapshots_unique_commit_per_repo(db_session):
    """Verifies that duplicates for (repository_id, commit_hash) are rejected."""
    repo_id = db_session.execute(text("INSERT INTO repositories (name, url) VALUES ('ru', 'u') RETURNING id")).scalar()
    # First insert succeeds
    db_session.execute(text(f"INSERT INTO snapshots (repository_id, commit_hash) VALUES ('{repo_id}', 'c1')"))
    # Second insert with same commit hash for same repo must fail
    with pytest.raises(IntegrityError):
        db_session.execute(text(f"INSERT INTO snapshots (repository_id, commit_hash) VALUES ('{repo_id}', 'c1')"))

def test_files_unique_path_per_snapshot(db_session):
    """Verifies that a snapshot cannot contain two files with the same path."""
    repo_id, snap_id = create_hierarchy(db_session, '_fu')
    db_session.execute(text(f"INSERT INTO files (snapshot_id, path, language, size_bytes) VALUES ('{snap_id}', 'a.py', 'py', 10)"))
    # Duplicate path insert must fail
    with pytest.raises(IntegrityError):
        db_session.execute(text(f"INSERT INTO files (snapshot_id, path, language, size_bytes) VALUES ('{snap_id}', 'a.py', 'py', 10)"))

def test_code_chunks_fk_to_contents_restrict(db_session):
    """Verifies that deleting 'contents' is RESTRICTED if referenced by 'code_chunks'."""
    repo_id, snap_id = create_hierarchy(db_session, '_fk')
    file_id = db_session.execute(text(f"INSERT INTO files (snapshot_id, path, language, size_bytes) VALUES ('{snap_id}', 'f', 'py', 10) RETURNING id")).scalar()
    h = 'hfk'
    # Create content blob
    db_session.execute(text(f"INSERT INTO contents (chunk_hash, content, size_bytes) VALUES ('{h}', 'c', 1)"))
    # Link chunk to content
    db_session.execute(text(f"INSERT INTO code_chunks (file_id, chunk_hash, byte_range) VALUES ('{file_id}', '{h}', '[0, 1)')"))
    
    # Attempting to delete the content blob should fail due to FK restriction
    with pytest.raises(IntegrityError):
        db_session.execute(text(f"DELETE FROM contents WHERE chunk_hash='{h}'"))

def test_fk_cascade_snapshot_deletes_all(db_session):
    """Verifies that deleting a snapshot cascades to files, chunks, symbols, and other child tables."""
    repo_id, snap_id = create_hierarchy(db_session, '_casc')
    file_id = db_session.execute(text(f"INSERT INTO files (snapshot_id, path, language, size_bytes) VALUES ('{snap_id}', 'f', 'py', 10) RETURNING id")).scalar()
    h = 'hcasc'
    db_session.execute(text(f"INSERT INTO contents (chunk_hash, content, size_bytes) VALUES ('{h}', 'c', 1)"))
    db_session.execute(text(f"INSERT INTO code_chunks (file_id, chunk_hash, byte_range) VALUES ('{file_id}', '{h}', '[0, 1)')"))
    db_session.execute(text(f"INSERT INTO symbols (file_id, snapshot_id, kind, name, byte_range) VALUES ('{file_id}', '{snap_id}', 'variable', 'x', '[0, 1)')"))
    
    # Delete the snapshot
    db_session.execute(text(f"DELETE FROM snapshots WHERE id='{snap_id}'"))
    
    # Verify child records are gone
    assert db_session.execute(text(f"SELECT count(*) FROM files WHERE snapshot_id='{snap_id}'")).scalar() == 0
    assert db_session.execute(text(f"SELECT count(*) FROM symbols WHERE snapshot_id='{snap_id}'")).scalar() == 0

# =================================================================
# 3) Trigger: repositories.updated_at
# =================================================================

def test_repositories_updated_at_changes_on_update(db_session):
    """Verifies that 'updated_at' is automatically updated when the repository row changes."""
    repo_id = db_session.execute(text("INSERT INTO repositories (name, url) VALUES ('r_trig', 'u') RETURNING id")).scalar()
    t1 = db_session.execute(text(f"SELECT updated_at FROM repositories WHERE id='{repo_id}'")).scalar()
    
    time.sleep(0.01) # Ensure time difference
    
    # Update row
    db_session.execute(text(f"UPDATE repositories SET name='r_trig2' WHERE id='{repo_id}'"))
    t2 = db_session.execute(text(f"SELECT updated_at FROM repositories WHERE id='{repo_id}'")).scalar()
    
    # Timestamp should have increased
    assert t2 > t1

# =================================================================
# 4) Trigger: Consistency between Symbols, Files, and Snapshots
# =================================================================

def create_full_hierarchy(db_session, suffix=''):
    """Helper creating repo, snapshot, and file, returning their IDs."""
    repo_id, snap_id = create_hierarchy(db_session, suffix)
    file_id = db_session.execute(text(f"INSERT INTO files (snapshot_id, path, language, size_bytes) VALUES ('{snap_id}', 'f', 'py', 10) RETURNING id")).scalar()
    return repo_id, snap_id, file_id

def test_symbols_snapshot_matches_file_snapshot_insert(db_session):
    """Verifies failure when inserting a symbol linked to a file but with a different snapshot_id."""
    _, snap_id, file_id = create_full_hierarchy(db_session, '_sym_m')
    other_repo, other_snap = create_hierarchy(db_session, '_sym_m_other')
    
    # Attempt insert mismatching snapshot_id
    with pytest.raises((InternalError, IntegrityError), match="symbols.snapshot_id must match"):
        db_session.execute(text(f"""
            INSERT INTO symbols (file_id, snapshot_id, kind, name, byte_range)
            VALUES ('{file_id}', '{other_snap}', 'function', 'bad', '[0, 10)')
        """))

def test_symbols_snapshot_matches_file_snapshot_update(db_session):
    """Verifies constraint enforcement when updating a symbol's snapshot_id to an inconsistent state."""
    _, snap_id, file_id = create_full_hierarchy(db_session, '_sym_u')
    sym_id = db_session.execute(text(f"""
        INSERT INTO symbols (file_id, snapshot_id, kind, name, byte_range)
        VALUES ('{file_id}', '{snap_id}', 'function', 'ok', '[0, 10)') RETURNING id
    """)).scalar()
    
    _, other_snap = create_hierarchy(db_session, '_sym_u_other')
    
    # Attempt update to mismatched snapshot
    with pytest.raises((InternalError, IntegrityError), match="symbols.snapshot_id must match"):
        db_session.execute(text(f"UPDATE symbols SET snapshot_id='{other_snap}' WHERE id='{sym_id}'"))

def test_symbols_self_parent_constraint(db_session):
    """Verifies that a symbol cannot be its own parent."""
    _, snap_id, file_id = create_full_hierarchy(db_session, '_sym_p')
    sym_id = db_session.execute(text(f"""
        INSERT INTO symbols (file_id, snapshot_id, kind, name, byte_range)
        VALUES ('{file_id}', '{snap_id}', 'function', 'ok', '[0, 10)') RETURNING id
    """)).scalar()
    
    # Set parent_id = id
    with pytest.raises(IntegrityError):
         db_session.execute(text(f"UPDATE symbols SET parent_id='{sym_id}' WHERE id='{sym_id}'"))

# =================================================================
# 5) Trigger: Consistency between Embeddings and References
# =================================================================

def create_chunk_context(db_session, suffix=''):
    """Helper ensuring existance of repo, snapshot, file, content, and code_chunk."""
    r, s, f = create_full_hierarchy(db_session, suffix)
    h = f'hc{suffix}'
    db_session.execute(text(f"INSERT INTO contents (chunk_hash, content, size_bytes) VALUES ('{h}', 'c', 1)"))
    c = db_session.execute(text(f"""
        INSERT INTO code_chunks (file_id, chunk_hash, byte_range)
        VALUES ('{f}', '{h}', '[0, 1)') RETURNING id
    """)).scalar()
    return r, s, f, c

def test_embeddings_refs_match_chunk_file_and_snapshot_insert(db_session):
    """Verifies failure when inserting embedding with mismatching snapshot/file IDs compared to its chunk."""
    r, s, f, c = create_chunk_context(db_session, '_emi')
    _, other_s = create_hierarchy(db_session, '_emi_other')
    
    # Mismatch checking
    with pytest.raises((InternalError, IntegrityError), match="embeddings snapshot_id/file_id must match"):
        db_session.execute(text(f"""
            INSERT INTO code_chunk_embeddings (chunk_id, snapshot_id, file_id, vector_hash, embedding, model)
            VALUES ('{c}', '{other_s}', '{f}', 'vh', '{vec()}', 'm')
        """))

def test_embeddings_refs_match_chunk_file_and_snapshot_update(db_session):
    """Verifies constraint enforcement when updating embedding references to inconsistent state."""
    r, s, f, c = create_chunk_context(db_session, '_emu')
    emb = db_session.execute(text(f"""
        INSERT INTO code_chunk_embeddings (chunk_id, snapshot_id, file_id, vector_hash, embedding, model)
        VALUES ('{c}', '{s}', '{f}', 'vh', '{vec()}', 'm') RETURNING id
    """)).scalar()
    
    _, other_s = create_hierarchy(db_session, '_emu_other')
    
    with pytest.raises((InternalError, IntegrityError), match="embeddings snapshot_id/file_id must match"):
        db_session.execute(text(f"UPDATE code_chunk_embeddings SET snapshot_id='{other_s}' WHERE id='{emb}'"))

# =================================================================
# 6) Embedding Ingestion Idempotency
# =================================================================

def test_embeddings_unique_chunk_model(db_session):
    """Verifies that we cannot insert duplicate embeddings for the same (chunk_id, model)."""
    r, s, f, c = create_chunk_context(db_session, '_eunk')
    db_session.execute(text(f"""
        INSERT INTO code_chunk_embeddings (chunk_id, snapshot_id, file_id, vector_hash, embedding, model)
        VALUES ('{c}', '{s}', '{f}', 'vh', '{vec(1)}', 'm')
    """))
    # Second insert with same model for same chunk must fail
    with pytest.raises(IntegrityError):
        db_session.execute(text(f"""
            INSERT INTO code_chunk_embeddings (chunk_id, snapshot_id, file_id, vector_hash, embedding, model)
            VALUES ('{c}', '{s}', '{f}', 'vh2', '{vec(2)}', 'm')
        """))

def test_embeddings_cache_lookup_works(db_session):
    """Verifies we can perform efficient lookups on (vector_hash, model) using the index."""
    r, s, f, c = create_chunk_context(db_session, '_ecache')
    db_session.execute(text(f"""
        INSERT INTO code_chunk_embeddings (chunk_id, snapshot_id, file_id, vector_hash, embedding, model)
        VALUES ('{c}', '{s}', '{f}', 'hashXYZ', '{vec(0.1)}', 'gpt-4')
    """))
    # Simulate cache hit attempt
    res = db_session.execute(text("SELECT embedding FROM code_chunk_embeddings WHERE vector_hash='hashXYZ' AND model='gpt-4'")).fetchone()
    assert res is not None

# =================================================================
# 7) FTS: Population and Update Triggers
# =================================================================

def test_fts_row_created_on_chunk_insert(db_session):
    """Verifies that inserting a chunk automatically populates the FTS table via trigger."""
    r, s, f = create_full_hierarchy(db_session, '_fts_i')
    h = 'h_fts_i'
    db_session.execute(text(f"INSERT INTO contents (chunk_hash, content, size_bytes) VALUES ('{h}', 'hello', 5)"))
    # Insert code chunk
    c = db_session.execute(text(f"INSERT INTO code_chunks (file_id, chunk_hash, byte_range) VALUES ('{f}', '{h}', '[0, 5)') RETURNING id")).scalar()
    
    # Check if FTS vector was created
    fts = db_session.execute(text(f"SELECT search_vector FROM code_chunk_fts WHERE chunk_id='{c}'")).scalar()
    # Ensure keyword 'hello' is indexed
    assert "'hello'" in str(fts)

def test_fts_updates_on_chunk_update(db_session):
    """Verifies that changing a chunk's hash (referencing new content) updates the FTS vector."""
    r, s, f = create_full_hierarchy(db_session, '_fts_u')
    h1, h2 = 'h_fts_u1', 'h_fts_u2'
    # Insert two different contents
    db_session.execute(text(f"INSERT INTO contents (chunk_hash, content, size_bytes) VALUES ('{h1}', 'apple', 5)"))
    db_session.execute(text(f"INSERT INTO contents (chunk_hash, content, size_bytes) VALUES ('{h2}', 'banana', 6)"))
    
    # Expect full word 'apple' since we use 'simple' dictionary (no stemming)
    c = db_session.execute(text(f"INSERT INTO code_chunks (file_id, chunk_hash, byte_range) VALUES ('{f}', '{h1}', '[0, 5)') RETURNING id")).scalar()
    fts_str = str(db_session.execute(text(f"SELECT search_vector FROM code_chunk_fts WHERE chunk_id='{c}'")).scalar())
    assert "'apple'" in fts_str
    db_session.execute(text(f"UPDATE code_chunks SET chunk_hash='{h2}' WHERE id='{c}'"))
    # Keyword matches should change
    assert "'banana'" in str(db_session.execute(text(f"SELECT search_vector FROM code_chunk_fts WHERE chunk_id='{c}'")).scalar())

def test_fts_snapshot_and_file_consistency(db_session):
    """Verifies that the FTS table correctly inherits valid snapshot/file IDs."""
    r, s, f = create_full_hierarchy(db_session, '_fts_c')
    h = 'h_fts_c'
    db_session.execute(text(f"INSERT INTO contents (chunk_hash, content, size_bytes) VALUES ('{h}', 'x', 1)"))
    c = db_session.execute(text(f"INSERT INTO code_chunks (file_id, chunk_hash, byte_range) VALUES ('{f}', '{h}', '[0, 1)') RETURNING id")).scalar()
    
    row = db_session.execute(text(f"SELECT snapshot_id, file_id FROM code_chunk_fts WHERE chunk_id='{c}'")).fetchone()
    # IDs must match inheritance
    assert str(row[0]) == str(s) and str(row[1]) == str(f)

# =================================================================
# 8) Indexes: Existence and Usage (Smoke Tests)
# =================================================================

def test_indexes_exist(db_session):
    """Verifies that critical indexes are actually created in the database."""
    rows = db_session.execute(text("SELECT indexname FROM pg_indexes WHERE schemaname='public'")).fetchall()
    indexes = {r[0] for r in rows}
    required = {'ix_code_chunks_spatial', 'ix_files_path_pattern', 'ix_embeddings_cache_key', 'ix_embeddings_vector'}
    assert required.issubset(indexes), f"Missing indexes: {required-indexes}"

def test_query_plan_uses_cache_key_index(db_session):
    """Verifies that the planner uses the 'ix_embeddings_cache_key' for cache lookups."""
    # Temporarily disable sequential scans to force index usage preference if applicable
    db_session.execute(text("SET enable_seqscan = off"))
    plan = db_session.execute(text("EXPLAIN SELECT embedding FROM code_chunk_embeddings WHERE vector_hash='x' AND model='y'")).fetchall()
    plan_str = "\n".join([r[0] for r in plan])
    # The plan should mention the index scan
    assert "ix_embeddings_cache_key" in plan_str
    
def test_query_plan_uses_hnsw_for_knn(db_session):
    """Verifies that HNSW index is used for vector similarity search (ORDER BY <->)."""
    db_session.execute(text("SET enable_seqscan = off"))
    # KNN queries using <=> operator
    plan = db_session.execute(text(f"EXPLAIN SELECT id FROM code_chunk_embeddings ORDER BY embedding <=> '{vec()}' LIMIT 5")).fetchall()
    plan_str = "\n".join([r[0] for r in plan])
    # Should use the vector index
    assert "ix_embeddings_vector" in plan_str

# =================================================================
# 9) Query Behavior: Snapshot Scoping
# =================================================================

def test_vector_search_returns_only_snapshot_results(db_session):
    """Verifies that queries filtered by snapshot_id do not return results from other snapshots."""
    r, s1, f1, c1 = create_chunk_context(db_session, '_scope1')
    db_session.execute(text(f"INSERT INTO code_chunk_embeddings (chunk_id, snapshot_id, file_id, vector_hash, embedding, model) VALUES ('{c1}', '{s1}', '{f1}', 'vh1', '{vec(1)}', 'm')"))
    
    # Snapshot 2
    _, s2, f2, c2 = create_chunk_context(db_session, '_scope2')
    db_session.execute(text(f"INSERT INTO code_chunk_embeddings (chunk_id, snapshot_id, file_id, vector_hash, embedding, model) VALUES ('{c2}', '{s2}', '{f2}', 'vh2', '{vec(2)}', 'm')"))
    
    # Query constrained to snapshot 1
    res = db_session.execute(text(f"SELECT chunk_id FROM code_chunk_embeddings WHERE snapshot_id='{s1}'")).fetchall()
    assert len(res) == 1
    assert str(res[0][0]) == str(c1)

def test_spatial_queries_on_chunks(db_session):
    """Verifies spatial overlap queries (&&) for finding chunks containing a byte offset."""
    r, s, f, c = create_chunk_context(db_session, '_spatial')
    # Chunk range is [0, 1)
    
    # Check overlap with range [0, 1)
    res = db_session.execute(text(f"SELECT id FROM code_chunks WHERE file_id='{f}' AND byte_range && int4range(0, 1)")).scalar()
    assert res is not None
    
    # Check non-overlapping range [10, 20)
    res_empty = db_session.execute(text(f"SELECT id FROM code_chunks WHERE file_id='{f}' AND byte_range && int4range(10, 20)")).scalar()
    assert res_empty is None

def test_spatial_queries_on_symbols(db_session):
    """Verifies spatial overlap queries work similarly for symbols (navigation)."""
    r, s, f, c = create_chunk_context(db_session, '_spatial_sym')
    db_session.execute(text(f"INSERT INTO symbols (file_id, snapshot_id, kind, name, byte_range) VALUES ('{f}', '{s}', 'variable', 'x', '[10, 20)')"))
    
    # Overlap with [15, 25) should catch the symbol at [10, 20)
    count = db_session.execute(text(f"SELECT count(*) FROM symbols WHERE file_id='{f}' AND byte_range && int4range(15, 25)")).scalar()
    assert count == 1

# =================================================================
# 10) Garbage Collection Correctness
# =================================================================

def test_gc_delete_snapshot_cleans_everything(db_session):
    """Verifies that deleting a snapshot cleans up all related embeddings."""
    r, s, f, c = create_chunk_context(db_session, '_gc')
    db_session.execute(text(f"INSERT INTO code_chunk_embeddings (chunk_id, snapshot_id, file_id, vector_hash, embedding, model) VALUES ('{c}', '{s}', '{f}', 'vh', '{vec()}', 'm')"))
    
    db_session.execute(text(f"DELETE FROM snapshots WHERE id='{s}'"))
    
    cnt = db_session.execute(text(f"SELECT count(*) FROM code_chunk_embeddings WHERE snapshot_id='{s}'")).scalar()
    assert cnt == 0

def test_gc_preserves_other_snapshots(db_session):
    """Verifies that garbage collecting one snapshot does not affect others."""
    r, s1, f1, c1 = create_chunk_context(db_session, '_gc_keep1')
    r, s2, f2, c2 = create_chunk_context(db_session, '_gc_keep2')
    
    db_session.execute(text(f"DELETE FROM snapshots WHERE id='{s1}'"))
    
    # S2 contents should remain
    assert db_session.execute(text(f"SELECT count(*) FROM files WHERE snapshot_id='{s2}'")).scalar() > 0

# =================================================================
# 11) Concurrency and Race Conditions
# =================================================================

def get_engine():
    """Helper to create independent engines for threads."""
    return create_engine(TEST_DB_URL, pool_size=5, isolation_level="AUTOCOMMIT")

def test_embedding_insert_race_idempotent(db_engine):
    """
    Simulates a race condition where multiple workers try to insert the same embedding.
    Verifies that only one succeeds (idempotency) and others fail safely.
    """
    # Setup shared state manually
    with db_engine.connect() as conn:
        r_id = conn.execute(text("INSERT INTO repositories (name, url) VALUES ('race_repo', 'u') RETURNING id")).scalar()
        s_id = conn.execute(text(f"INSERT INTO snapshots (repository_id, commit_hash) VALUES ('{r_id}', 'race') RETURNING id")).scalar()
        f_id = conn.execute(text(f"INSERT INTO files (snapshot_id, path, language, size_bytes) VALUES ('{s_id}', 'race.py', 'py', 10) RETURNING id")).scalar()
        h = 'hrace'
        conn.execute(text(f"INSERT INTO contents (chunk_hash, content, size_bytes) VALUES ('{h}', 'c', 1)"))
        c_id = conn.execute(text(f"INSERT INTO code_chunks (file_id, chunk_hash, byte_range) VALUES ('{f_id}', '{h}', '[0, 1)') RETURNING id")).scalar()

    def try_insert():
        # Each thread gets its own connection
        eng = get_engine()
        with eng.connect() as conn:
            try:
                conn.execute(text(f"""
                    INSERT INTO code_chunk_embeddings (chunk_id, snapshot_id, file_id, vector_hash, embedding, model)
                    VALUES ('{c_id}', '{s_id}', '{f_id}', 'vh_race', '{vec()}', 'model_race')
                """))
                return "inserted"
            except IntegrityError:
                return "collision"
            except Exception as e:
                return str(e)
            finally:
                eng.dispose()

    # Launch threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(try_insert) for _ in range(5)]
        results = [f.result() for f in futures]
    
    # Logic verification
    assert "inserted" in results
    inserted_count = results.count("inserted")
    assert inserted_count == 1, f"Only one insert should succeed, but got {inserted_count}"
    
    # DB State verification
    with db_engine.connect() as conn:
        cnt = conn.execute(text(f"SELECT count(*) FROM code_chunk_embeddings WHERE model='model_race'")).scalar()
        assert cnt == 1

def test_cache_hit_race(db_engine):
    """
    Simulates two workers doing a cache check-then-insert flow.
    Ensures that strictly one record is created despite the race window.
    """
    with db_engine.connect() as conn:
        r = conn.execute(text("INSERT INTO repositories (name, url) VALUES ('race_cache', 'u') RETURNING id")).scalar()
        s = conn.execute(text(f"INSERT INTO snapshots (repository_id, commit_hash) VALUES ('{r}', 'rc') RETURNING id")).scalar()
        f = conn.execute(text(f"INSERT INTO files (snapshot_id, path, language, size_bytes) VALUES ('{s}', 'rc.py', 'py', 10) RETURNING id")).scalar()
        h = 'hrc'
        conn.execute(text(f"INSERT INTO contents (chunk_hash, content, size_bytes) VALUES ('{h}', 'c', 1)"))
        c = conn.execute(text(f"INSERT INTO code_chunks (file_id, chunk_hash, byte_range) VALUES ('{f}', '{h}', '[0, 1)') RETURNING id")).scalar()

    def worker_logic():
        eng = get_engine()
        with eng.connect() as conn:
            try:
                # 1. Lookup (Cache Check)
                res = conn.execute(text(f"SELECT id FROM code_chunk_embeddings WHERE vector_hash='vh_rc' AND model='m_rc'")).scalar()
                if not res:
                    # Simulate small processing delay to widen race window
                    time.sleep(0.05)
                    # 2. Insert (Cache Miss)
                    conn.execute(text(f"""
                        INSERT INTO code_chunk_embeddings (chunk_id, snapshot_id, file_id, vector_hash, embedding, model)
                        VALUES ('{c}', '{s}', '{f}', 'vh_rc', '{vec()}', 'm_rc')
                    """))
                    return "inserted"
                return "found"
            except IntegrityError:
                return "collision"
            finally:
                eng.dispose()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker_logic) for _ in range(2)]
        results = [f.result() for f in futures]
    
    # Should maintain exactly one record
    assert "inserted" in results
    with db_engine.connect() as conn:
        cnt = conn.execute(text("SELECT count(*) FROM code_chunk_embeddings WHERE model='m_rc'")).scalar()
        assert cnt == 1
