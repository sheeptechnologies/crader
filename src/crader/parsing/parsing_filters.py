"""
Centralized Configuration for Indexing Exclusion Rules.

This module defines "Noise Control" policies.
It prevents the indexer from wasting cycles on compiled binaries, package manager lockfiles,
or generated code that dilutes the semantic quality of the Knowledge Graph.

**Categories**:
*   `GLOBAL_IGNORE_DIRS`: Technical noise (git, node_modules) -> Never indexed.
*   `SEMANTIC_NOISE_DIRS`: Low-value code (fixtures, translations) -> Indexed by Parser but skipped by graph builders (configurable).
*   `LANGUAGE_SPECIFIC_FILTERS`: Fine-grained file pattern rejections via `fnmatch`.
"""

# Directories that are ALWAYS ignored (Technical Noise)
GLOBAL_IGNORE_DIRS = {
    ".git",
    ".svn",
    ".hg",
    ".cvs",
    ".vscode",
    ".idea",
    ".eclipse",
    ".settings",
    "node_modules",
    "venv",
    ".venv",
    "env",
    ".env",
    "site-packages",
    "jspm_packages",
    "bower_components",
    "dist",
    "build",
    "out",
    "target",
    "bin",
    "obj",
    "wheels",
    "eggs",
    ".eggs",
    "develop-eggs",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".npm",
    ".yarn",
    ".cache",
    ".coverage",
    "htmlcov",
    "logs",
    "tmp",
    "temp",
}

# Directories containing code but with low structural value (Semantic Noise)
# SCIP should ignore these to avoid bloating the graph.
SEMANTIC_NOISE_DIRS = {
    "migrations",
    "fixture",
    "fixtures",
    "mock",
    "mocks",
    "spec",
    "specs",  # Common test dirs
    "locales",
    "translations",
    "vendor",
    "assets",
    "static",
    "public",
    "docs",
    "documentation",
    "examples",
    "*test*",
}

# Language-specific specific configuration
LANGUAGE_SPECIFIC_FILTERS = {
    "python": {
        "exclude_patterns": [
            "conftest.py",
            "manage.py",
            "wsgi.py",
            "asgi.py",
            "setup.py",
            "alembic/versions/*",
        ],  # "*/test/*","*test*" "test_*.py", "*_test.py",
        "exclude_extensions": {".pyc", ".pyo", ".pyd", ".pyi"},
    },
    "javascript": {
        "exclude_patterns": [
            "*.test.js",
            "*test*",
            "*.spec.js",
            "*.min.js",
            "*.bundle.js",
            "webpack.config.js",
            "rollup.config.js",
        ],
        "exclude_extensions": {".map", ".d.ts"},
    },
    "java": {"exclude_patterns": ["src/test/*", "*Test.java"], "exclude_extensions": {".class", ".jar", ".war"}},
    "go": {"exclude_patterns": ["*_test.go", "vendor/*"], "exclude_extensions": {".exe"}},
    "web": {
        "exclude_patterns": ["package-lock.json", "yarn.lock"],
        "exclude_extensions": {".css.map", ".js.map", ".ico", ".svg", ".png", ".jpg"},
    },
}

MAX_FILE_SIZE_BYTES = 1 * 1024 * 1024
MAX_LINE_LENGTH = 1000
