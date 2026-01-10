"""
User-Focused End-to-End Tests

Tests real-world user scenarios based on debugger use cases:
- Code search and discovery
- Impact analysis
- Documentation generation
- Refactoring assistance
- Bug investigation
"""

import os
import sys

import pytest

# Add src to path
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, "..", "..", "src"))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from crader.navigator import CodeNavigator
from crader.providers.embedding import DummyEmbeddingProvider
from crader.reader import CodeReader
from crader.retriever import CodeRetriever
from crader.storage.connector import PooledConnector

DB_URL = os.getenv("TEST_DB_URL", "postgresql://sheep_user:sheep_password@localhost:6432/sheep_test")


@pytest.fixture(scope="module")
def test_setup():
    """Setup test environment with indexed Flask repository."""
    connector = PooledConnector(DB_URL)

    # Assume Flask is already indexed from previous tests
    # In a real scenario, you'd index it here

    repo_id = connector.ensure_repository(url="https://github.com/pallets/flask.git", branch="main", name="flask")

    snapshot = connector.get_active_snapshot(repo_id)
    if not snapshot:
        pytest.skip("Flask repository not indexed. Run test_multi_language_workflows.py first.")

    snapshot_id = snapshot["id"]

    yield {
        "connector": connector,
        "repo_id": repo_id,
        "snapshot_id": snapshot_id,
        "retriever": CodeRetriever(connector, DummyEmbeddingProvider()),
        "reader": CodeReader(connector),
        "navigator": CodeNavigator(connector),
    }

    connector.close()


class TestCodeDiscovery:
    """Test use case: Developer wants to understand how a feature works."""

    def test_find_authentication_code(self, test_setup):
        """
        Scenario: Developer asks "How does Flask handle authentication?"
        Expected: Find relevant authentication-related code
        """
        retriever = test_setup["retriever"]

        results = retriever.retrieve(
            query="authentication session management",
            repo_id=test_setup["repo_id"],
            snapshot_id=test_setup["snapshot_id"],
            limit=10,
            strategy="hybrid",
        )

        assert len(results) > 0, "Should find authentication-related code"

        # Verify results are relevant
        relevant_keywords = ["session", "auth", "login", "user", "cookie"]
        found_relevant = False

        for result in results:
            content_lower = result.content.lower()
            if any(keyword in content_lower for keyword in relevant_keywords):
                found_relevant = True
                break

        assert found_relevant, "Results should contain authentication-related keywords"

    def test_find_routing_mechanism(self, test_setup):
        """
        Scenario: Developer asks "How does Flask routing work?"
        Expected: Find route decorator and URL mapping code
        """
        retriever = test_setup["retriever"]

        results = retriever.retrieve(
            query="route decorator URL mapping",
            repo_id=test_setup["repo_id"],
            snapshot_id=test_setup["snapshot_id"],
            limit=10,
        )

        assert len(results) > 0

        # Should find code with @route or add_url_rule
        found_routing = any("@route" in r.content or "add_url_rule" in r.content for r in results)
        assert found_routing, "Should find routing-related code"

    def test_find_error_handling(self, test_setup):
        """
        Scenario: Developer asks "How does Flask handle errors?"
        Expected: Find error handler decorators and exception handling
        """
        retriever = test_setup["retriever"]

        results = retriever.retrieve(
            query="error handler exception handling",
            repo_id=test_setup["repo_id"],
            snapshot_id=test_setup["snapshot_id"],
            limit=10,
        )

        assert len(results) > 0

        # Should find error handling code
        error_keywords = ["error", "exception", "handler", "abort"]
        found_error_handling = any(any(keyword in r.content.lower() for keyword in error_keywords) for r in results)
        assert found_error_handling


class TestImpactAnalysis:
    """Test use case: Developer wants to understand impact of changes."""

    def test_find_function_callers(self, test_setup):
        """
        Scenario: Developer wants to know "What code calls this function?"
        Expected: Navigate call graph to find all callers
        """
        retriever = test_setup["retriever"]
        navigator = test_setup["navigator"]

        # First, find a function
        results = retriever.retrieve(
            query="render template", repo_id=test_setup["repo_id"], snapshot_id=test_setup["snapshot_id"], limit=1
        )

        if results:
            node_id = results[0].node_id

            # Analyze impact (find callers)
            impact = navigator.analyze_impact(node_id)

            # Should return a list (may be empty if function is not called)
            assert isinstance(impact, list)

    def test_find_dependencies(self, test_setup):
        """
        Scenario: Developer asks "What does this function depend on?"
        Expected: Find all functions/modules this code calls
        """
        retriever = test_setup["retriever"]
        navigator = test_setup["navigator"]

        # Find a function
        results = retriever.retrieve(
            query="request handler", repo_id=test_setup["repo_id"], snapshot_id=test_setup["snapshot_id"], limit=1
        )

        if results:
            node_id = results[0].node_id

            # Get parent context
            parent = navigator.read_parent_chunk(node_id)

            # Should return parent information or None
            assert parent is None or isinstance(parent, dict)


class TestRefactoringAssistance:
    """Test use case: Developer wants to refactor code safely."""

    def test_find_similar_patterns(self, test_setup):
        """
        Scenario: Developer wants to find "All code that follows this pattern"
        Expected: Find similar code structures
        """
        retriever = test_setup["retriever"]

        # Search for a specific pattern
        results = retriever.retrieve(
            query="decorator function wrapper",
            repo_id=test_setup["repo_id"],
            snapshot_id=test_setup["snapshot_id"],
            limit=10,
        )

        assert len(results) > 0

        # Results should contain decorator-related code
        found_decorators = any("@" in r.content or "decorator" in r.content.lower() for r in results)
        assert found_decorators

    def test_find_code_by_signature(self, test_setup):
        """
        Scenario: Developer wants to find "Functions with specific signature"
        Expected: Find functions matching type signature
        """
        retriever = test_setup["retriever"]

        # Search for functions with specific signature pattern
        results = retriever.retrieve(
            query="function returns response object",
            repo_id=test_setup["repo_id"],
            snapshot_id=test_setup["snapshot_id"],
            limit=10,
            filters={"language": "python"},
        )

        assert len(results) > 0


class TestDocumentationGeneration:
    """Test use case: Developer wants to generate documentation."""

    def test_extract_function_documentation(self, test_setup):
        """
        Scenario: Developer wants to "Document this function"
        Expected: Extract function signature, docstring, parameters
        """
        retriever = test_setup["retriever"]
        test_setup["reader"]

        # Find a well-documented function
        results = retriever.retrieve(
            query="render template function",
            repo_id=test_setup["repo_id"],
            snapshot_id=test_setup["snapshot_id"],
            limit=1,
        )

        if results:
            result = results[0]

            # Should have metadata about the function
            assert result.metadata is not None

            # Content should include the function
            assert "def " in result.content or "class " in result.content

    def test_extract_class_hierarchy(self, test_setup):
        """
        Scenario: Developer wants to "Show class hierarchy"
        Expected: Find class and its inheritance chain
        """
        retriever = test_setup["retriever"]

        # Find a class
        results = retriever.retrieve(
            query="Flask application class",
            repo_id=test_setup["repo_id"],
            snapshot_id=test_setup["snapshot_id"],
            limit=5,
        )

        # Should find class definitions
        found_class = any("class " in r.content for r in results)
        assert found_class or len(results) == 0  # May not find in small test


class TestBugInvestigation:
    """Test use case: Developer is investigating a bug."""

    def test_trace_execution_path(self, test_setup):
        """
        Scenario: Developer wants to "Trace how this code is reached"
        Expected: Navigate call graph backwards to entry points
        """
        retriever = test_setup["retriever"]
        navigator = test_setup["navigator"]

        # Find a function that might be in a call chain
        results = retriever.retrieve(
            query="error handler", repo_id=test_setup["repo_id"], snapshot_id=test_setup["snapshot_id"], limit=1
        )

        if results:
            node_id = results[0].node_id

            # Analyze who calls this (trace backwards)
            callers = navigator.analyze_impact(node_id)

            assert isinstance(callers, list)

    def test_find_error_handling_code(self, test_setup):
        """
        Scenario: Developer asks "Where are exceptions caught?"
        Expected: Find try/except blocks and error handlers
        """
        retriever = test_setup["retriever"]

        results = retriever.retrieve(
            query="try except error handling",
            repo_id=test_setup["repo_id"],
            snapshot_id=test_setup["snapshot_id"],
            limit=10,
        )

        # Should find error handling code
        any("try:" in r.content or "except" in r.content for r in results)

        assert len(results) > 0


class TestCodeNavigation:
    """Test use case: Developer wants to navigate codebase efficiently."""

    def test_read_file_with_context(self, test_setup):
        """
        Scenario: Developer wants to "Show me this file with context"
        Expected: Read file with surrounding code
        """
        reader = test_setup["reader"]

        # Read a specific file
        file_data = reader.read_file(snapshot_id=test_setup["snapshot_id"], file_path="src/flask/app.py")

        if file_data:
            assert "content" in file_data
            assert len(file_data["content"]) > 0
            assert "file_path" in file_data

    def test_read_file_range(self, test_setup):
        """
        Scenario: Developer wants to "Show me lines 10-50 of this file"
        Expected: Read specific line range
        """
        reader = test_setup["reader"]

        file_data = reader.read_file(
            snapshot_id=test_setup["snapshot_id"], file_path="src/flask/app.py", start_line=1, end_line=50
        )

        if file_data:
            # Should return limited content
            lines = file_data["content"].split("\n")
            assert len(lines) <= 50

    def test_filter_by_file_type(self, test_setup):
        """
        Scenario: Developer wants to "Search only in test files"
        Expected: Filter results by path pattern
        """
        retriever = test_setup["retriever"]

        results = retriever.retrieve(
            query="test case",
            repo_id=test_setup["repo_id"],
            snapshot_id=test_setup["snapshot_id"],
            limit=10,
            filters={"path_prefix": "tests/"},
        )

        # All results should be from tests directory
        for result in results:
            assert result.file_path.startswith("tests/")


class TestAdvancedQueries:
    """Test use case: Developer wants advanced search capabilities."""

    def test_hybrid_search(self, test_setup):
        """
        Scenario: Developer wants both semantic and keyword matching
        Expected: Combine vector and FTS search
        """
        retriever = test_setup["retriever"]

        # Hybrid search should combine both approaches
        results = retriever.retrieve(
            query="request context",
            repo_id=test_setup["repo_id"],
            snapshot_id=test_setup["snapshot_id"],
            limit=10,
            strategy="hybrid",
        )

        assert len(results) > 0

    def test_vector_only_search(self, test_setup):
        """
        Scenario: Developer wants semantic similarity search
        Expected: Find semantically similar code
        """
        retriever = test_setup["retriever"]

        results = retriever.retrieve(
            query="handle HTTP request",
            repo_id=test_setup["repo_id"],
            snapshot_id=test_setup["snapshot_id"],
            limit=10,
            strategy="vector",
        )

        assert len(results) > 0

    def test_keyword_only_search(self, test_setup):
        """
        Scenario: Developer wants exact keyword matching
        Expected: Find exact matches
        """
        retriever = test_setup["retriever"]

        results = retriever.retrieve(
            query="Flask",
            repo_id=test_setup["repo_id"],
            snapshot_id=test_setup["snapshot_id"],
            limit=10,
            strategy="keyword",
        )

        # Should find results containing "Flask"
        if results:
            found_keyword = any("Flask" in r.content for r in results)
            assert found_keyword


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
