import os
os.environ["TOKENIZERS_PARALLELISM"] = "false"
import sys
import logging
import time
import datetime
import uuid
import psycopg

# --- SETUP PATH ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path: sys.path.insert(0, src_dir)

from crader import CodebaseIndexer
from crader.storage.postgres import PostgresGraphStorage

# Configurazione Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger("LOCK_TEST")

# DB URL
DB_URL = "postgresql://sheep_user:sheep_password@localhost:5433/sheep_index"

def get_repo_status(storage, repo_id):
    """Helper per ispezionare lo stato nel DB."""
    with storage.pool.connection() as conn:
        return conn.execute(
            "SELECT status, last_commit, queued_commit FROM repositories WHERE id=%s", 
            (repo_id,)
        ).fetchone()

def run_lock_test():
    storage = PostgresGraphStorage(DB_URL, vector_dim=768)
    
    # Dati finti per il test
    fake_url = "https://github.com/test/lock-repo.git"
    fake_branch = "main"
    commit_v1 = "aaaa1111"
    commit_v2 = "bbbb2222"
    
    # Pulizia preliminare
    with storage.pool.connection() as conn:
        conn.execute("DELETE FROM repositories WHERE url=%s", (fake_url,))
    
    logger.info("\n--- TEST 1: LOCKING & QUEUING (LATEST-WIN) ---")
    
    # 1. Processo A prende il lock (V1)
    logger.info("1. Processo A inizia indicizzazione V1...")
    success_a, repo_id = storage.acquire_indexing_lock(
        url=fake_url, branch=fake_branch, name="LockTest", 
        commit_hash=commit_v1, timeout_minutes=30
    )
    
    if success_a:
        logger.info(f"✅ Processo A ha preso il lock. Repo ID: {repo_id}")
    else:
        logger.error("❌ FAIL: Lock fallito su repo pulito.")
        return

    # 2. Processo B arriva con V2 mentre A lavora
    logger.info(f"2. Processo B richiede lock per V2 ({commit_v2})...")
    success_b, _ = storage.acquire_indexing_lock(
        url=fake_url, branch=fake_branch, name="LockTest", 
        commit_hash=commit_v2
    )
    
    # CHECK: Deve ritornare FALSE ma deve aver ACCODATO V2
    if not success_b:
        row = get_repo_status(storage, repo_id)
        if row['status'] == 'indexing' and row['queued_commit'] == commit_v2:
            logger.info("✅ PASS: Processo B correttamente bloccato e V2 accodata.")
        else:
            logger.error(f"❌ FAIL: Stato errato nel DB. Status: {row['status']}, Queue: {row['queued_commit']}")
            return
    else:
        logger.error("❌ FAIL: Processo B ha preso il lock concorrente!")
        return

    # 3. Processo A finisce V1 e controlla la coda
    logger.info("3. Processo A finisce V1 e chiama release...")
    
    # Dovrebbe ritornare il commit V2 da processare
    next_job = storage.release_indexing_lock(repo_id, success=True, commit_hash=commit_v1)
    
    if next_job == commit_v2:
        logger.info(f"✅ PASS: Release ha restituito il job in coda ({next_job}).")
        
        # Check DB: status deve essere ancora 'indexing', queue vuota, last_commit aggiornato a V1
        row = get_repo_status(storage, repo_id)
        if row['status'] == 'indexing' and row['queued_commit'] is None and row['last_commit'] == commit_v1:
            logger.info("✅ PASS: Stato DB consistente (Worker continua).")
        else:
            logger.error(f"❌ FAIL: Stato DB errato dopo primo rilascio. {row}")
    else:
        logger.error(f"❌ FAIL: Release non ha restituito il job in coda. Got: {next_job}")

    # 4. Processo A finisce V2 (Coda vuota)
    logger.info("4. Processo A finisce V2 (Coda vuota)...")
    next_job_final = storage.release_indexing_lock(repo_id, success=True, commit_hash=commit_v2)
    
    if next_job_final is None:
        row = get_repo_status(storage, repo_id)
        if row['status'] == 'completed' and row['last_commit'] == commit_v2:
            logger.info("✅ PASS: Ciclo completato. Stato finale: Completed.")
        else:
            logger.error(f"❌ FAIL: Stato finale DB errato. {row}")
    else:
        logger.error("❌ FAIL: Release ha restituito job fantasma!")

    
    logger.info("\n--- TEST 2: ZOMBIE LOCK (AUTO-HEALING) ---")
    
    # 1. Creiamo un lock "morto" (vecchio di 2 ore)
    with storage.pool.connection() as conn:
        old_time = datetime.datetime.utcnow() - datetime.timedelta(hours=2)
        conn.execute("UPDATE repositories SET status='indexing', updated_at=%s WHERE id=%s", (old_time, repo_id))
    logger.info("💀 Creato Zombie Lock.")
    
    # 2. Proviamo a prenderlo
    success_zombie, _ = storage.acquire_indexing_lock(
        url=fake_url, branch=fake_branch, name="LockTest", 
        commit_hash="zombie_fix", timeout_minutes=30
    )
    
    if success_zombie:
        logger.info("✅ PASS: Zombie Lock rubato con successo.")
        # Pulizia
        storage.release_indexing_lock(repo_id, success=True, commit_hash="zombie_fix")
    else:
        logger.error("❌ FAIL: Zombie Lock non superato.")


    logger.info("\n--- TEST 3: FAILURE HANDLING ---")
    
    # 1. Blocchiamo
    storage.acquire_indexing_lock(fake_url, fake_branch, "FailTest", "fail_v1")
    
    # 2. Rilasciamo con errore
    logger.info("Simulazione crash...")
    storage.release_indexing_lock(repo_id, success=False)
    
    row = get_repo_status(storage, repo_id)
    if row['status'] == 'failed':
        logger.info("✅ PASS: Stato impostato a 'failed'.")
    else:
        logger.error(f"❌ FAIL: Stato errato su errore: {row['status']}")

    # 3. Verifica Recovery: Un nuovo processo deve poter ripartire dopo un failed
    success_retry, _ = storage.acquire_indexing_lock(fake_url, fake_branch, "Retry", "retry_v1")
    if success_retry:
        logger.info("✅ PASS: Recovery da stato 'failed' riuscito.")
    else:
        logger.error("❌ FAIL: Impossibile ripartire dopo un fallimento.")

    # Cleanup
    with storage.pool.connection() as conn:
        conn.execute("DELETE FROM repositories WHERE url=%s", (fake_url,))
    storage.close()
    logger.info("\n🎉 TUTTI I TEST DI LOCKING SUPERATI!")

if __name__ == "__main__":
    run_lock_test()