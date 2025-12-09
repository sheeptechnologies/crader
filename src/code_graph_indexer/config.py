import os
from pathlib import Path

# --- CONFIGURAZIONE STORAGE ---

# 1. Cerchiamo la variabile d'ambiente 'REPO_VOLUME' (settata da Docker/Kubernetes o .env)
# 2. Se manca, usiamo una cartella locale './sheep_data/repositories' relativa alla CWD.
DEFAULT_LOCAL_PATH = os.path.join(os.getcwd(), "sheep_data", "repositories")
STORAGE_ROOT = os.getenv("REPO_VOLUME", DEFAULT_LOCAL_PATH)

# Garantiamo che il path sia assoluto
STORAGE_ROOT = os.path.abspath(STORAGE_ROOT)

# Assicuriamoci che la root esista (Fail-fast se non abbiamo permessi)
try:
    os.makedirs(STORAGE_ROOT, exist_ok=True)
except OSError as e:
    # Logghiamo ma non crashiamo qui, crasha chi prova a scriverci
    print(f"⚠️ Warning: Impossibile creare STORAGE_ROOT in {STORAGE_ROOT}: {e}")