# Unit Test Documentation

This guide explains the structure and purpose of the unit tests in the **Crader** project.

## 📊 Current Coverage

**Total Coverage: 73%** (target: >90%)

All 100 tests passing ✅

## 📁 Test Structure

### `tests/unit/test_scip_indexer_unit.py`
**What it tests**: SCIP (SCIP Code Intelligence Protocol) index processing

**Coverage areas**:
- `test_clean_symbol_logic`: SCIP symbol name cleaning (removing special characters)
- `test_bytes_conversion`: Line/column coordinate → byte offset conversion
- `test_extract_symbol_name`: Symbol name extraction from source code
- `test_process_definitions`: Symbol definition processing
- `test_scip_runner_prepare_indices`: Index preparation via external SCIP tool
- `test_stream_documents`: JSON document streaming from SCIP indices

**Testing techniques**:
- Mock `subprocess` to simulate SCIP tool execution
- Mock filesystem operations (`os.path.exists`, `os.path.getsize`)
- Patch internal methods to isolate logic

### `tests/unit/test_parser_unit.py`
**What it tests**: Source code parsing with TreeSitter

**Coverage areas**:
- `test_chunking_with_overlap`: Code splitting into chunks with overlap
- `test_recursive_chunking_small_file`: Small file chunking
- `test_handle_large_node_breakdown`: Handling AST nodes exceeding size limits
- `test_extract_tags`: Semantic tag extraction (functions, classes)
- `test_should_process_file`: File filtering logic

**Key concepts**:
- **Chunking**: Splitting code into manageable pieces for embedding
- **Overlap**: Maintaining context between adjacent chunks
- **Semantic captures**: Metadata extracted from AST (function name, type, etc.)
- **AST (Abstract Syntax Tree)**: Structured representation of code

**Testing techniques**:
- Mock TreeSitter nodes with `MagicMock`
- Custom `DummyNode` class to avoid comparison errors
- Patch `_extract_tags` to isolate chunking logic

### `tests/unit/test_storage_postgres.py`
**What it tests**: Code graph persistence in PostgreSQL

**Coverage areas**:
- **Repository & Snapshot**:
  - `test_ensure_repository_new`: New repository registration
  - `test_create_snapshot_new`: Snapshot creation
  - `test_create_snapshot_existing`: Existing snapshot reuse
  
- **Data Insertion**:
  - `test_add_files`: File insertion
  - `test_add_nodes_fast`: Bulk insert with COPY protocol
  - `test_add_contents_raw`: Content insertion
  
- **Search**:
  - `test_search_vectors`: Semantic vector search
  - `test_search_fts`: Full-text search
  - `test_get_incoming_definitions_bulk`: Batch definition retrieval
  
- **Graph**:
  - `test_get_context_neighbors`: Neighbor node navigation
  - `test_ingest_scip_relations`: SCIP relation ingestion

**Key concepts**:
- **Snapshot**: Repository state at a specific point in time
- **COPY protocol**: High-performance bulk insert in PostgreSQL
- **Context manager**: `with` pattern for transaction management
- **Bulk operations**: Batch operations to reduce DB round-trips

**Testing techniques**:
- Mock `psycopg` (PostgreSQL driver)
- Mock `uuid.uuid4` for deterministic results
- `side_effect` to simulate call sequences
- SQL verification with `assertIn` for flexibility

### `tests/unit/test_indexer_lifecycle.py`
**What it tests**: Complete indexing process orchestration

**Coverage areas**:
- `test_initialization`: Component initialization
- `test_index_workflow_success`: Complete indexing workflow
- `test_index_skip_existing`: Skip already-indexed snapshots
- `test_embed_pipeline`: Embedding pipeline

**Key concepts**:
- **Workflow**: Repository → Snapshot → Parse → Embed → Swap
- **Concurrency**: Using `ProcessPoolExecutor` for parallelization
- **Namespace injection**: Manual mock injection to avoid import side-effects

### `tests/unit/test_git_volume.py`
**What it tests**: Git repository management

**Coverage areas**:
- `test_ensure_repo_updated`: Repository clone/fetch
- `test_get_head_commit`: HEAD commit retrieval

## 🛠️ Mocking Techniques

### 1. Basic Mock
```python
mock_obj = MagicMock()
mock_obj.method.return_value = "value"
```

### 2. Context Manager
```python
mock_copy_manager = MagicMock()
mock_copy_obj = MagicMock()
mock_copy_manager.__enter__.return_value = mock_copy_obj
```

### 3. Side Effects (Sequences)
```python
mock_cursor.fetchone.side_effect = [
    {"id": "1"},  # First call
    None,         # Second call
]
```

### 4. Patch Decorator
```python
@patch("module.function")
def test_something(self, mock_function):
    mock_function.return_value = "mocked"
```

## 🎯 Best Practices

1. **Isolate dependencies**: Mock DB, filesystem, subprocess
2. **Deterministic tests**: Mock UUID, timestamps, random
3. **Flexible assertions**: `assertIn` instead of `assertEqual` for SQL
4. **Descriptive comments**: Explain WHAT and WHY, not just HOW
5. **Descriptive names**: `test_create_snapshot_new` > `test_snapshot_1`

## 🐛 Debugging Tests

### Test fails with `TypeError: '<' not supported`
**Cause**: Comparison between `MagicMock` objects  
**Solution**: Use `DummyNode` class with concrete attributes

### Test fails with `AttributeError: 'NoneType' object`
**Cause**: Mock not configured correctly  
**Solution**: Verify `return_value` or `side_effect`

### Test fails with `AssertionError: Expected 'execute' to be called`
**Cause**: Assertion on wrong mock (`mock_cursor` vs `mock_conn`)  
**Solution**: Check which object actually executes the query

## 📈 Next Steps for 90% Coverage

1. **Expand `scip.py` tests**: Cover relation processing methods
2. **Test `schema.py`**: Currently 0% coverage
3. **Test `embedding.py`**: Increase from 68% to >90%
4. **Test `git_volume_manager.py`**: Cover `files()` method and filtering

## 🔗 Resources

- [pytest Documentation](https://docs.pytest.org/)
- [unittest.mock Guide](https://docs.python.org/3/library/unittest.mock.html)
- [TreeSitter](https://tree-sitter.github.io/tree-sitter/)
- [SCIP Protocol](https://github.com/sourcegraph/scip)
