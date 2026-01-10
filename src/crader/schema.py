from typing import Literal

# ==========================================
# SEMATIC VOCABULARY (Constants)
# ==========================================

# Functional roles of the code (derived from semantic analysis .scm)
# These values must EXACTLY match the tags used in queries/*.scm files
# and the fallbacks in parser.py.
# This vocabulary allows the frontend to apply distinct icons/styles to nodes.
VALID_ROLES = Literal[
    "entry_point",  # Application Root (e.g., if __name__ == "__main__", func main())
    "api_endpoint",  # Network Interface (e.g., @app.get, @router.post)
    "test_case",  # Unit Logic (e.g., def test_..., @test)
    "test_suite",  # Group of Tests (e.g., class Test..., describe())
    "data_schema",  # Data Structure (e.g., @dataclass, Pydantic BaseModel, struct)
    "class",  # Generic Class Definition (fallback)
    "function",  # Generic Function Definition (fallback)
    "method",  # Class Method (fallback)
    "module",  # File/Module Scope
]

# Macroscopic categories
# Used for high-level filtering (e.g., "Don't index tests", "Show only docs").
# 1. Derived from File System (MetadataProvider): test, config, docs, code
# 2. Derived from Semantics (Parser): logic, definition
VALID_CATEGORIES = Literal[
    "test",  # Test files or chunks
    "config",  # Configuration/Env files
    "docs",  # Documentation (Markdown, RST)
    "code",  # Generic Source Code (Default for files)
    "logic",  # Algorithm/Control Flow (Default for unknown chunks)
    "definition",  # Type Definitions, Interfaces, Schemas
]
