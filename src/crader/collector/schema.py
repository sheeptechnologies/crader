from dataclasses import dataclass
from typing import Optional, Literal

# Semantic categories for files
FileCategory = Literal['source', 'test', 'config', 'docs', 'unknown']

@dataclass(slots=True)
class CollectedFile:
    """
    Represents a validated, sanitized, and enriched source file.
    Output of the SourceCollector stream.
    """
    rel_path: str         # Path relative to repo root (e.g., "src/main.py")
    full_path: str        # Absolute path on disk (for content reading)
    extension: str        # Normalized extension (e.g., ".py")
    size_bytes: int       # File size in bytes
    git_hash: Optional[str] # SHA-1 Blob ID (None if untracked/dirty)
    category: FileCategory  # Semantic classification (e.g., 'test')

    @property
    def is_tracked(self) -> bool:
        """Returns True if the file is tracked by Git and has a valid hash."""
        return self.git_hash is not None