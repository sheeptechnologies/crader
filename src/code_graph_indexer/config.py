# import os

# Definisce la radice dello storage. 
# In produzione sarà settata da Docker (es. /mnt/data/repositories).
# In locale fa fallback su una cartella ./sheep_data
# STORAGE_ROOT = os.getenv("REPO_VOLUME", os.path.abspath("./sheep_data/repositories"))