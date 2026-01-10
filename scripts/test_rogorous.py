import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- SETUP PATH ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, "..", "src"))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from crader import CodebaseIndexer, CodeReader
from crader.storage.postgres import PostgresGraphStorage

# Configurazione Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("RIGOROUS_TEST")

# --- CONFIG ---
DB_PORT = "5433"
DB_URL = f"postgresql://sheep_user:sheep_password@localhost:{DB_PORT}/sheep_index"
REPO_URL = "https://github.com/pallets/flask.git"
REPO_PATH = "/tmp/flask_rigorous_test"

# --- HELPER UTILS ---
def setup_env():
    if not os.path.exists(REPO_PATH):
        logger.info("⬇️ Cloning Flask...")
        os.system(f"git clone --depth 1 {REPO_URL} {REPO_PATH} > /dev/null 2>&1")
    # Timeout 30s per gestire lo stress test
    return PostgresGraphStorage(DB_URL, min_size=5, max_size=20, timeout=30.0)

def worker_index_task(storage, force=False):
    """Esegue indexing e ritorna l'ID snapshot."""
    indexer = CodebaseIndexer(REPO_PATH, storage)
    try:
        return indexer.index(force=force)
    except Exception as e:
        logger.error(f"❌ Index Error: {e}")
        return None

def worker_read_task(storage, snapshot_id):
    """Tenta di leggere un file critico con logging degli errori."""
    reader = CodeReader(storage)
    try:
        # Cerchiamo la cartella src/flask
        items = reader.list_directory(snapshot_id, "src/flask")
        if not items:
            logger.warning(f"⚠️  READ FAIL: Directory src/flask vuota o non trovata in {snapshot_id}")
            return False

        # [FIX CRITICO] Cerchiamo un FILE, non una cartella
        target_file = None
        for item in items:
            if item['type'] == 'file' and item['name'].endswith('.py'):
                target_file = item['path']
                break

        if not target_file:
            # Fallback se non ci sono py file
            target_file = next((i['path'] for i in items if i['type'] == 'file'), None)

        if not target_file:
            logger.warning("⚠️  READ FAIL: Nessun file leggibile trovato in src/flask")
            return False

        # Leggiamo il file trovato
        data = reader.read_file(snapshot_id, target_file, start_line=1, end_line=5)

        if len(data['content']) > 0:
            return True
        else:
            logger.warning(f"⚠️  READ FAIL: File {target_file} vuoto.")
            return False

    except Exception as e:
        logger.error(f"❌ READ EXCEPTION: {e}")
        return False

# ==============================================================================
# TEST SUITE
# ==============================================================================

def test_1_stampede(storage):
    """SCENARIO: 20 utenti cliccano 'Index' contemporaneamente."""
    logger.info("\n🧪 TEST 1: THE STAMPEDE (Idempotenza & Locking)")

    start_time = time.time()
    snapshots = []

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(worker_index_task, storage, False) for _ in range(20)]
        for f in as_completed(futures):
            res = f.result()
            if res: snapshots.append(res)

    elapsed = time.time() - start_time
    unique_snaps = set(snapshots)

    print(f"   ⏱️  Tempo Totale: {elapsed:.2f}s")
    print(f"   📊 Risultati: {len(snapshots)}/20 completati.")
    print(f"   🔑 IDs univoci: {len(unique_snaps)} -> {unique_snaps}")

    if len(unique_snaps) == 1 and len(snapshots) == 20:
        logger.info("✅ PASS: Stampede gestito.")
        return list(unique_snaps)[0]
    else:
        logger.error("❌ FAIL: Race Condition!")
        return None

def test_3_pool_stress(storage, snap_id):
    """SCENARIO: 50 lettori concorrenti su pool da 20."""
    logger.info("\n🧪 TEST 3: CONNECTION POOL SATURATION")

    logger.info(f"   🔍 Verifica preliminare Snapshot {snap_id}...")

    # Verifichiamo se ci sono file leggendo davvero
    if not worker_read_task(storage, snap_id):
        logger.warning("   ⚠️ Snapshot corrotto/vuoto (o errore lettura). Tentativo di ripristino...")
        idx = CodebaseIndexer(REPO_PATH, storage)
        snap_id = idx.index(force=True)
        logger.info(f"   ✅ Ripristinato. Nuovo ID: {snap_id}")

    logger.info("   🚀 Lancio 50 letture concorrenti...")
    start = time.time()
    successes = 0

    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(worker_read_task, storage, snap_id) for _ in range(50)]
        for f in as_completed(futures):
            if f.result(): successes += 1

    elapsed = time.time() - start
    print(f"   ⏱️  Tempo: {elapsed:.2f}s")
    print(f"   📊 Successi: {successes}/50")

    if successes == 50:
        logger.info("✅ PASS: Pool stress test superato.")
    else:
        logger.error(f"❌ FAIL: {50-successes} richieste fallite (Vedi log sopra per dettagli).")

def main():
    logger.info("🚀 STARTING RIGOROUS VALIDATION SUITE (DEBUG MODE)")

    try:
        storage = setup_env()

        # Test 1
        snap_id = test_1_stampede(storage)

        if snap_id:
            # Test 3
            test_3_pool_stress(storage, snap_id)

        logger.info("\n🏁 ALL TESTS COMPLETED.")
        storage.close()

    except Exception as e:
        logger.critical(f"🔥 CRITICAL FAILURE: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
