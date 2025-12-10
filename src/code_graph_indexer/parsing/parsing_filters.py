"""
Configurazione centralizzata per il filtraggio dei file.
"""

# Directory che vanno SEMPRE ignorate (Rumore Tecnico)
GLOBAL_IGNORE_DIRS = {
    ".git", ".svn", ".hg", ".cvs",
    ".vscode", ".idea", ".eclipse", ".settings",
    "node_modules", "venv", ".venv", "env", ".env", 
    "site-packages", "jspm_packages", "bower_components",
    "dist", "build", "out", "target", "bin", "obj", 
    "wheels", "eggs", ".eggs", "develop-eggs",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".npm", ".yarn", ".cache", ".coverage", "htmlcov",
    "logs", "tmp", "temp"
}

# Directory che contengono codice ma poco valore strutturale (Rumore Semantico)
# SCIP dovrebbe ignorarle per non appesantire il grafo.
SEMANTIC_NOISE_DIRS = {
    "migrations", "fixture", "fixtures",
    "mock", "mocks", "test", "tests", "spec", "specs", # Test dir comuni
    "locales", "translations",
    "vendor", "assets", "static", "public",
    "docs", "documentation", "examples"
}

# Configurazione specifica per linguaggio
LANGUAGE_SPECIFIC_FILTERS = {
    "python": {
        "exclude_patterns": ["*/test/*", "test_*.py", "*_test.py", "conftest.py", "manage.py", "wsgi.py", "asgi.py", "setup.py", "alembic/versions/*"],
        "exclude_extensions": {".pyc", ".pyo", ".pyd", ".pyi"}
    },
    "javascript": {
        "exclude_patterns": ["*.test.js", "*.spec.js", "*.min.js", "*.bundle.js", "webpack.config.js", "rollup.config.js"],
        "exclude_extensions": {".map", ".d.ts"}
    },
    "java": {
        "exclude_patterns": ["src/test/*", "*Test.java"],
        "exclude_extensions": {".class", ".jar", ".war"}
    },
    "go": {
        "exclude_patterns": ["*_test.go", "vendor/*"],
        "exclude_extensions": {".exe"}
    },
    "web": {
        "exclude_patterns": ["package-lock.json", "yarn.lock"],
        "exclude_extensions": {".css.map", ".js.map", ".ico", ".svg", ".png", ".jpg"}
    }
}

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
MAX_LINE_LENGTH = 1000