"""
Configuration for the SourceCollector module.
Defines inclusion/exclusion rules and system limits for file ingestion.
"""

# Hard limit for single file size (1MB).
# Files larger than this are often minified code, huge CSVs, or masked binaries.
MAX_FILE_SIZE_BYTES = 1024 * 1024 

# Supported Extensions (Allow-list).
# Only include formats that TreeSitter or the embedding model can reasonably process.
SUPPORTED_EXTENSIONS = {
    # Backend / Systems / Scripting
    '.py', '.pyi', '.go', '.rs', '.java', '.kt', '.scala',
    '.c', '.cc', '.cpp', '.h', '.hpp', '.cs', '.php', '.rb',
    # Frontend / Web
    '.js', '.jsx', '.ts', '.tsx', '.vue', '.svelte',
    '.css', '.scss', '.html',
    # Config / Data / Docs
    '.json', '.yaml', '.yml', '.toml', '.xml', '.sql', '.md', '.rst'
}

# Directories to ALWAYS ignore (Safety Net beyond .gitignore).
# These prevent indexing of dependencies, build artifacts, or environments.
BLOCKLIST_DIRS = {
    '.git', '.svn', '.hg', '.idea', '.vscode',
    'node_modules', 'venv', '.venv', 'env',
    'dist', 'build', 'target', 'out', 'bin',
    '__pycache__', 'coverage', '.pytest_cache',
    'vendor', 'third_party' 
}