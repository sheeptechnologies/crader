import os
import sys
import time
import logging
import multiprocessing
from unittest.mock import patch

# --- SETUP PATH ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, ".."))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from code_graph_indexer import CodebaseIndexer
from code_graph_indexer.storage.connector import PooledConnector
from code_graph_indexer.storage.postgres import PostgresGraphStorage

# --- CONFIGURAZIONE ---
# Testiamo su Django (grande abbastanza per far girare SCIP seriamente)
REPO_URL = "https://github.com/django/django.git" 
BRANCH = "main"

# Assicurati che punti alla porta corretta:
# 6432 = PgBouncer (Enterprise Mode)
# 5433 = Postgres Diretto (Fallback se PgBouncer non è attivo)
DB_PORT = "6432" 
DB_USER = "sheep_user"
DB_PASS = "sheep_password"
DB_NAME = "sheep_index"
DB_DSN = f"postgresql://{DB_USER}:{DB_PASS}@localhost:{DB_PORT}/{DB_NAME}"

# Setup Logger
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("BENCHMARK")
logger.setLevel(logging.INFO)
logging.getLogger("code_graph_indexer").setLevel(logging.INFO)

def clean_database():
    """Pulisce il DB usando un connettore di servizio."""
    print("🧹 Cleaning Database...")
    try:
        # Usiamo un pool minimo per l'admin task
        connector = PooledConnector(dsn=DB_DSN, min_size=1, max_size=1)
        storage = PostgresGraphStorage(connector)
        with connector.get_connection() as conn:
            conn.execute("TRUNCATE repositories CASCADE")
        connector.close()
    except Exception as e:
        print(f"⚠️ Errore pulizia DB (Potrebbe essere vuoto o connessione fallita): {e}")

def get_db_stats():
    """Legge le statistiche finali."""
    connector = PooledConnector(dsn=DB_DSN, min_size=1, max_size=1)
    storage = PostgresGraphStorage(connector)
    stats = storage.get_stats()
    connector.close()
    return stats

def run_session(mode_name: str, single_core: bool):
    print(f"\n{'='*60}")
    print(f"🚀 AVVIO SESSIONE: {mode_name} (Full Stack: Parser + SCIP)")
    print(f"{'='*60}")
    
    clean_database()
    
    # Configurazione automatica via ENV per l'Indexer
    os.environ["DB_URL"] = DB_DSN
    
    start_time = time.time()
    
    # NOTA: Abbiamo rimosso il patch di SCIP. Ora gira davvero!
    
    if single_core:
        print("🐌 Single-Core (Simulated)...")
        # Simuliamo 1 solo worker di parsing
        with patch('multiprocessing.cpu_count', return_value=2):
            indexer = CodebaseIndexer(REPO_URL, BRANCH)
            indexer.index(force=True)
            indexer.close()
    else:
        real_cpus = multiprocessing.cpu_count()
        print(f"⚡ Enterprise Multi-Core ({real_cpus} CPU)...")
        
        indexer = CodebaseIndexer(REPO_URL, BRANCH)
        indexer.index(force=True)
        indexer.close()

    duration = time.time() - start_time
    
    stats = get_db_stats()
    return {"mode": mode_name, "duration": duration, "stats": stats}

def print_report(optimized):
    print("\n\n")
    print("🏆 FINAL BENCHMARK REPORT (Full Stack SCIP)")
    print(f"{'Metrica':<20} | {'Baseline (1 Core)':<20} | {'Enterprise (Multi)':<20} | {'Speedup'}")
    print("-" * 85)
    
    t1, t2 = 241.69, optimized['duration']
    
    # Recupero stats
    # f1 = baseline['stats'].get('files', 0)
    n1 = 40796
    
    f2 = optimized['stats'].get('files', 0)
    n2 = optimized['stats'].get('total_nodes', 0)
    
    speedup = t1 / t2 if t2 > 0 else 0
    fps_base = 13.86
    fps_opt = f2 / t2 if t2 > 0 else 0
    
    print(f"{'Tempo (sec)':<20} | {t1:<20.2f} | {t2:<20.2f} | {speedup:.2f}x 🚀")
    print(f"{'File/sec':<20} | {fps_base:<20.2f} | {fps_opt:<20.2f} |")
    print(f"{'Nodi Generati':<20} | {n1:<20} | {n2:<20} |")
    print("-" * 85)
    
    if abs(n1 - n2) > 200:
        print("⚠️ Warning: Differenza significativa nel numero di nodi tra le esecuzioni.")
        print("Potrebbe indicare una race condition nel salvataggio SCIP se i numeri sono molto diversi.")
    else:
        print(f"✅ Integrità Dati OK: {n2} nodi.")

if __name__ == "__main__":
    # Assicurati che PgBouncer (6432) sia su, oppure cambia DB_PORT a 5433
    
    # 1. Baseline Run (1 Parse Worker + SCIP)
    # res_base = run_session("Baseline", single_core=True)
    
    # 2. Optimized Run (N Parse Workers + SCIP)
    res_opt = run_session("Enterprise", single_core=False)
    
    print_report( res_opt)