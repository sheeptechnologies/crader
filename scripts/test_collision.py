import os
import sys
import shutil
import logging

os.environ["TOKENIZERS_PARALLELISM"] = "false"
# --- SETUP PATH ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path: sys.path.insert(0, src_dir)

from crader import CodebaseIndexer
from crader.storage.postgres import PostgresGraphStorage
from crader.providers.embedding import FastEmbedProvider

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("COLLISION_TEST")

# DB URL (Verifica la porta)
DB_URL = "postgresql://sheep_user:sheep_password@localhost:5433/sheep_index"

def setup_local_repo(path, unique_content):
    if os.path.exists(path): shutil.rmtree(path)
    os.makedirs(path)
    
    # File unico per identificarla
    with open(os.path.join(path, "unique.py"), "w") as f:
        f.write(f"def {unique_content}(): pass")
        
    # Git init MA SENZA REMOTE
    import subprocess
    subprocess.run(["git", "init"], cwd=path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "add", "."], cwd=path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, stdout=subprocess.DEVNULL)

def run_test():
    path_a = os.path.abspath("temp_collision_a")
    path_b = os.path.abspath("temp_collision_b")
    
    setup_local_repo(path_a, "function_A")
    setup_local_repo(path_b, "function_B")
    
    storage = PostgresGraphStorage(DB_URL, vector_dim=768)
    provider = FastEmbedProvider()
    
    try:
        # 1. INDEX REPO A
        logger.info("🚀 Indexing Repo A (No Remote)...")
        idx_a = CodebaseIndexer(path_a, storage)
        idx_a.index(force=True)
        id_a = idx_a.parser.repo_id
        logger.info(f"👉 Repo A ID: {id_a}")
        
        # Verifica che A sia nel DB
        with storage.pool.connection() as conn:
            count_a = conn.execute(
                "SELECT count(*) as c FROM files WHERE repo_id=%s", (id_a,)
            ).fetchone()['c']
        logger.info(f"📊 Files in Repo A: {count_a}")

        # 2. INDEX REPO B
        logger.info("🚀 Indexing Repo B (No Remote)...")
        idx_b = CodebaseIndexer(path_b, storage)
        idx_b.index(force=True)
        id_b = idx_b.parser.repo_id
        logger.info(f"👉 Repo B ID: {id_b}")

        # --- VERIFICA DEL BUG ---
        if id_a == id_b:
            logger.error("❌ FAIL CRITICO: Le due repo hanno lo stesso ID! Collisione rilevata.")
            logger.error("   Il sistema pensa che siano la stessa repo e ha sovrascritto i dati.")
        else:
            logger.info("✅ PASS: ID diversi. Il sistema le distingue.")
            
            # Verifica finale che i dati di A esistano ancora
            with storage.pool.connection() as conn:
                check_a = conn.execute(
                    "SELECT count(*) as c FROM files WHERE repo_id=%s", (id_a,)
                ).fetchone()['c']
            
            if check_a > 0:
                logger.info(f"✅ PASS: I dati di Repo A sono ancora presenti ({check_a} files).")
            else:
                logger.error("❌ FAIL: I dati di Repo A sono SPARITI dopo aver indicizzato B!")

    except Exception as e:
        logger.error(f"Errore: {e}")
    finally:
        storage.close()
        if os.path.exists(path_a): shutil.rmtree(path_a)
        if os.path.exists(path_b): shutil.rmtree(path_b)

if __name__ == "__main__":
    run_test()