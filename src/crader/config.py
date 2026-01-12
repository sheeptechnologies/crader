import os

# ==============================================================================
#  STORAGE CONFIGURATION & DEFAULTS
# ==============================================================================

"""
Defines the runtime configuration for the Repository Volume Manager.

Scalability Note:
In a distributed environment (Kubernetes/Cloud), `REPO_VOLUME` should ideally point to a 
Shared Persistent Volume (NFS/EFS) to allow all worker pods to access the same raw git data.
"""

# 1. Look for 'CRADER_REPO_VOLUME' environment variable (set by Docker/Kubernetes or .env)
# 2. If missing, use a local folder './sheep_data/repositories' relative to CWD.
DEFAULT_LOCAL_PATH = os.path.join(os.getcwd(), "sheep_data", "repositories")
STORAGE_ROOT = os.getenv("CRADER_REPO_VOLUME", DEFAULT_LOCAL_PATH)

# Ensure the path is absolute
STORAGE_ROOT = os.path.abspath(STORAGE_ROOT)

# Ensure the root exists (Fail-fast if we don't have permissions)
try:
    os.makedirs(STORAGE_ROOT, exist_ok=True)
except OSError as e:
    # Log the warning but do not crash here; let the writer handle the crash
    print(f"⚠️ Warning: Unable to create STORAGE_ROOT at {STORAGE_ROOT}: {e}")
