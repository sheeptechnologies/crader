# End-to-End Tests

This directory contains comprehensive end-to-end tests that verify the complete workflow of the Sheep Codebase Indexer.

## Test Files

### `test_multi_language_workflows.py`
Tests the complete indexing and retrieval workflow across multiple programming languages:

- **Python (Flask)**: Web framework with routing, templates, sessions
- **TypeScript (React)**: Frontend framework with hooks, components
- **Go (Hugo)**: Static site generator

**Test Coverage**:
- Repository cloning
- Code parsing and indexing
- Embedding generation
- Semantic search
- File reading
- Graph navigation
- Incremental indexing
- Error handling

### `test_user_scenarios.py`
Tests real-world user scenarios based on common development tasks:

**Code Discovery**:
- Finding authentication code
- Understanding routing mechanisms
- Locating error handling

**Impact Analysis**:
- Finding function callers
- Identifying dependencies
- Tracing execution paths

**Refactoring Assistance**:
- Finding similar code patterns
- Locating code by signature
- Identifying refactoring candidates

**Documentation Generation**:
- Extracting function documentation
- Building class hierarchies
- Generating API docs

**Bug Investigation**:
- Tracing execution paths
- Finding error handlers
- Understanding code flow

**Code Navigation**:
- Reading files with context
- Filtering by file type
- Advanced search queries

## Running Tests

### Prerequisites

1. **PostgreSQL with pgvector**:
```bash
# Start PostgreSQL
docker run -d \
  --name sheep-test-db \
  -e POSTGRES_USER=sheep_user \
  -e POSTGRES_PASSWORD=sheep_password \
  -e POSTGRES_DB=sheep_test \
  -p 6432:5432 \
  pgvector/pgvector:pg16
```

2. **Environment Variables**:
```bash
export TEST_DB_URL="postgresql://sheep_user:sheep_password@localhost:6432/sheep_test"
export USE_REAL_EMBEDDINGS="false"  # Set to "true" to use OpenAI
export OPENAI_API_KEY="sk-..."  # Only if USE_REAL_EMBEDDINGS=true
```

### Run All E2E Tests

```bash
# From project root
pytest tests/e2e/ -v

# With detailed output
pytest tests/e2e/ -v -s

# Run specific test file
pytest tests/e2e/test_user_scenarios.py -v

# Run specific test class
pytest tests/e2e/test_user_scenarios.py::TestCodeDiscovery -v

# Run specific test
pytest tests/e2e/test_user_scenarios.py::TestCodeDiscovery::test_find_authentication_code -v
```

### Run with Coverage

```bash
pytest tests/e2e/ --cov=src/code_graph_indexer --cov-report=html
```

## Test Configuration

### Using Dummy Embeddings (Default)

By default, tests use `DummyEmbeddingProvider` which generates random vectors. This is fast and doesn't require API keys, but search quality will be lower.

```bash
export USE_REAL_EMBEDDINGS="false"
pytest tests/e2e/ -v
```

### Using Real Embeddings (OpenAI)

For more realistic testing with actual semantic search:

```bash
export USE_REAL_EMBEDDINGS="true"
export OPENAI_API_KEY="sk-..."
pytest tests/e2e/ -v
```

**Note**: This will make API calls and incur costs.

## Test Data

Tests clone real repositories:
- **Flask**: https://github.com/pallets/flask.git
- **React**: https://github.com/facebook/react.git
- **Hugo**: https://github.com/gohugoio/hugo.git

Repositories are cloned to temporary directories and cleaned up after tests.

## Performance

### Expected Duration

| Test Suite | Dummy Embeddings | Real Embeddings |
|------------|------------------|-----------------|
| `test_multi_language_workflows.py` | ~5 minutes | ~15 minutes |
| `test_user_scenarios.py` | ~2 minutes | ~5 minutes |

**Factors affecting duration**:
- Repository size
- Network speed (cloning)
- Database performance
- Embedding API latency (if using real embeddings)

### Optimization Tips

1. **Reuse indexed data**: Tests check if repositories are already indexed
2. **Parallel execution**: Use `pytest-xdist` for parallel tests
3. **Local repositories**: Clone repos once and reuse

```bash
# Parallel execution
pytest tests/e2e/ -v -n 4
```

## Troubleshooting

### Database Connection Errors

```bash
# Check PostgreSQL is running
pg_isready -h localhost -p 6432

# Verify pgvector extension
psql -h localhost -p 6432 -U sheep_user -d sheep_test -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### Git Clone Failures

```bash
# Check network connectivity
git clone --depth 1 https://github.com/pallets/flask.git /tmp/test_flask

# Use SSH if HTTPS fails
export GIT_PROTOCOL="ssh"
```

### Out of Memory

```bash
# Reduce batch sizes in tests
# Edit test files and reduce batch_size parameters

# Or increase Docker memory limit
docker update sheep-test-db --memory=4g
```

### Slow Tests

```bash
# Skip slow tests
pytest tests/e2e/ -v -m "not slow"

# Run only fast tests
pytest tests/e2e/test_user_scenarios.py -v
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: E2E Tests

on: [push, pull_request]

jobs:
  e2e-tests:
    runs-on: ubuntu-latest
    
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env:
          POSTGRES_USER: sheep_user
          POSTGRES_PASSWORD: sheep_password
          POSTGRES_DB: sheep_test
        ports:
          - 6432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install -e ".[dev]"
      
      - name: Run E2E tests
        env:
          TEST_DB_URL: postgresql://sheep_user:sheep_password@localhost:6432/sheep_test
          USE_REAL_EMBEDDINGS: false
        run: |
          pytest tests/e2e/ -v --cov=src/code_graph_indexer
```

## Writing New E2E Tests

### Template

```python
import pytest
from code_graph_indexer.indexer import CodebaseIndexer
from code_graph_indexer.retriever import CodeRetriever

class TestMyFeature:
    """Test my new feature."""
    
    @pytest.fixture(scope="class")
    def setup(self, db_connector):
        """Setup test data."""
        # Your setup code
        yield data
        # Cleanup
    
    def test_my_scenario(self, setup):
        """
        Scenario: User wants to do X
        Expected: System does Y
        """
        # Arrange
        retriever = setup['retriever']
        
        # Act
        results = retriever.retrieve(query="test")
        
        # Assert
        assert len(results) > 0
```

### Best Practices

1. **Use descriptive test names**: `test_find_authentication_code` not `test_search_1`
2. **Document scenarios**: Explain what user is trying to do
3. **Test real use cases**: Based on actual developer workflows
4. **Clean up resources**: Use fixtures for setup/teardown
5. **Make tests independent**: Don't rely on test execution order
6. **Use appropriate assertions**: Verify behavior, not implementation

## Contributing

When adding new E2E tests:

1. Follow existing patterns
2. Add documentation to this README
3. Ensure tests pass in CI
4. Consider performance impact
5. Update test coverage goals

## Support

For issues with E2E tests:
- Check [Troubleshooting](#troubleshooting) section
- Review test logs: `pytest tests/e2e/ -v -s --log-cli-level=DEBUG`
- Open an issue with full error output
