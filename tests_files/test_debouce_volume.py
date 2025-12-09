import os
import sys
import logging
import time
import concurrent.futures
from typing import Tuple

# --- SETUP PATH ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, "..", "src"))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from code_graph_indexer import CodebaseIndexer
from code_graph_indexer.storage.postgres import PostgresGraphStorage

# Configurazione Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("TEST_MULTI_BRANCH")

# --- CONFIGURAZIONE ---
DB_PORT = "5433"  # Verifica la tua porta (es. 5432 o 5433)
DB_URL = f"postgresql://sheep_user:sheep_password@localhost:{DB_PORT}/sheep_index"

REPO_URL = "https://github.com/pallets/flask.git"
STORE_PATH = "/tmp/sheep_test_store"  # Usiamo un path temporaneo per il test

# Definiamo i branch da testare (Simuliamo Main vs Stable)
BRANCHES = ["main", "3.1.2"] 

def run_indexing_job(branch_name: str) -> Tuple[str, str, float]:
    """
    Esegue l'intero ciclo di indicizzazione per un branch specifico.
    Ritorna: (branch, snapshot_id, duration)
    """
    start_time = time.time()
    thread_name = f"Worker-{branch_name}"
    
    logger.info(f"[{thread_name}] 🚀 Starting Job for branch '{branch_name}'...")
    
    try:
        # Ogni worker ha la sua istanza di storage (ma condividono il pool sottostante se ben configurato)
        # In un test reale, userebbero lo stesso pool globale. Qui ne creiamo uno per semplicità o usiamo quello globale.
        # Per stressare il pool, creiamo una connessione dedicata.
        storage = PostgresGraphStorage(DB_URL, min_size=1, max_size=5)
        
        indexer = CodebaseIndexer(
            repo_url=REPO_URL,
            branch=branch_name,
            storage=storage
        )
        
        # Avvia Indexing (gestisce fetch, lock, worktree, parsing)
        snapshot_id = indexer.index(force=False)
        
        elapsed = time.time() - start_time
        logger.info(f"[{thread_name}] ✅ Job Finished in {elapsed:.2f}s. Snapshot: {snapshot_id}")
        
        storage.close()
        return branch_name, snapshot_id, elapsed

    except Exception as e:
        logger.error(f"[{thread_name}] ❌ Job Failed: {e}", exc_info=True)
        return branch_name, None, 0.0

def main():
    logger.info("🚀 AVVIO TEST: FLASK MULTI-BRANCH (MAIN vs STABLE)")
    logger.info(f"📂 Store Path: {STORE_PATH}")
    
    # Pulizia preventiva opzionale (per forzare il re-download/re-index)
    # import shutil
    # if os.path.exists(STORE_PATH): shutil.rmtree(STORE_PATH)

    results = []
    
    # Eseguiamo i job in parallelo
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(BRANCHES)) as executor:
        futures = {executor.submit(run_indexing_job, branch): branch for branch in BRANCHES}
        
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            results.append(res)

    print("\n" + "="*60)
    print("📊 REPORT FINALE")
    print("="*60)
    
    success_count = 0
    snapshot_ids = set()
    
    for branch, snap_id, duration in results:
        status = "✅ OK" if snap_id and snap_id != "queued" else "❌ FAIL"
        if snap_id: 
            success_count += 1
            snapshot_ids.add(snap_id)
            
        print(f"Branch: {branch:<10} | Time: {duration:.2f}s | Status: {status} | ID: {snap_id}")

    print("-" * 60)
    
    # Verifiche
    if success_count == len(BRANCHES):
        print("✅ Tutti i branch sono stati indicizzati con successo.")
        
        if len(snapshot_ids) == 2:
            print("✅ Corretto: Sono stati creati 2 snapshot distinti (hash diversi per branch diversi).")
        elif len(snapshot_ids) == 1:
            print("⚠️ Warning: È stato creato un solo snapshot. I branch puntano allo stesso commit?")
        
        # Verifica Cache su Disco
        cache_path = os.path.join(STORE_PATH, "cache")
        workspaces_path = os.path.join(STORE_PATH, "workspaces")
        
        if os.path.exists(cache_path) and len(os.listdir(cache_path)) > 0:
            print("✅ Cache Repo trovata (Bare Repo).")
        else:
            print("❌ Errore: Cache repo non trovata.")
            
        if os.path.exists(workspaces_path) and not os.listdir(workspaces_path):
            print("✅ Workspaces puliti correttamente (Nessun residuo).")
        else:
            print(f"⚠️ Warning: Trovati residui in workspaces: {os.listdir(workspaces_path)}")
            
    else:
        print("❌ Alcuni job sono falliti.")

if __name__ == "__main__":
    main()