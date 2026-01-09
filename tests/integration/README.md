# Integration Tests

Fast integration tests using comprehensive mocks to verify workflows across multiple languages and use cases.

## Philosophy

These tests verify the **integration** of components without requiring:
- Real database connections
- Repository cloning
- Network access
- External API calls

This makes them:
- **Fast**: Run in seconds, not minutes
- **Reliable**: No external dependencies
- **Deterministic**: Same results every time

## Test Files

### `test_workflows.py`
Integration tests covering complete workflows with mocks:

**Multi-Language Support**:
- Python (Flask-like patterns)
- TypeScript (React-like patterns)
- Go (Hugo-like patterns)

**User Scenarios**:
- Code discovery (authentication, routing)
- Impact analysis (callers, dependencies)
- Refactoring assistance (patterns, signatures)
- File navigation
- Hybrid search

**Error Handling**:
- Empty results
- Invalid nodes
- Edge cases

## Running Tests

```bash
# Run all integration tests
pytest tests/integration/ -v

# Run specific test file
pytest tests/integration/test_workflows.py -v

# Run specific test class
pytest tests/integration/test_workflows.py::TestPythonWorkflow -v

# Run with coverage
pytest tests/integration/ --cov=src/code_graph_indexer
```

## Test Structure

### Fixtures

**`mock_storage`**: Mock storage connector with common methods
- Repository management
- Snapshot operations
- Search methods
- File reading

**`mock_embedding_provider`**: Mock embedding provider
- Async embedding generation
- Model configuration

### Test Classes

**`TestPythonWorkflow`**: Python codebase testing
- Index repository
- Search routing code
- Navigate call graph

**`TestTypeScriptWorkflow`**: TypeScript codebase testing
- Index TypeScript files
- Search hooks implementation

**`TestGoWorkflow`**: Go codebase testing
- Index Go repository
- Search functions

**`TestUserScenarios`**: Real-world use cases
- Authentication discovery
- Impact analysis
- Pattern finding
- Hybrid search

**`TestErrorHandling`**: Edge cases
- Empty results
- Invalid inputs

## Mock Patterns

### Storage Mock

```python
mock_storage = Mock()
mock_storage.ensure_repository.return_value = "repo_123"
mock_storage.search_vectors.return_value = [
    {
        'node_id': 'node_1',
        'content': 'def foo(): pass',
        'score': 0.95
    }
]
```

### Component Mocking

```python
with patch('code_graph_indexer.indexer.GitVolumeManager') as mock_git:
    mock_git.return_value.files.return_value = ["src/app.py"]
    # Test code
```

## Performance

Expected duration: **< 5 seconds** for all tests

## Comparison with E2E Tests

| Aspect | Integration Tests | E2E Tests |
|--------|------------------|-----------|
| **Speed** | < 5s | > 5 minutes |
| **Database** | Mocked | Real PostgreSQL |
| **Repositories** | Mocked | Real cloning |
| **Reliability** | 100% | Depends on network |
| **Purpose** | Verify integration | Verify real-world usage |

## When to Use

**Integration Tests** (this directory):
- During development
- In CI/CD pipelines
- For quick feedback
- Testing component integration

**E2E Tests** (`tests/e2e/`):
- Before releases
- Manual testing
- Validating real scenarios
- Performance testing

## Adding New Tests

```python
def test_my_scenario(mock_storage, mock_embedding_provider):
    """
    Scenario: User wants to do X
    Expected: System does Y
    """
    # Setup mocks
    mock_storage.search_vectors.return_value = [...]
    
    # Execute
    retriever = CodeRetriever(mock_storage, mock_embedding_provider)
    results = retriever.retrieve(query="test")
    
    # Assert
    assert len(results) > 0
```

## Best Practices

1. **Mock external dependencies**: Database, Git, APIs
2. **Use realistic data**: Mock responses should match real data structure
3. **Test behavior, not implementation**: Focus on outcomes
4. **Keep tests fast**: Avoid sleeps, use mocks
5. **Document scenarios**: Explain what user is trying to do

## Troubleshooting

### Import Errors

```bash
# Ensure src is in path
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"
pytest tests/integration/ -v
```

### Mock Not Working

```python
# Use correct patch path (where it's imported, not defined)
with patch('code_graph_indexer.indexer.GitVolumeManager'):  # ✅
with patch('code_graph_indexer.volume_manager.git_volume_manager.GitVolumeManager'):  # ❌
```

## Contributing

When adding integration tests:
1. Use existing fixtures
2. Follow naming conventions
3. Document scenarios
4. Keep tests fast (< 1s each)
5. Verify mocks match real behavior
