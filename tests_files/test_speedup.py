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
# Usa una repo sostanziosa per vedere la differenza (Django è perfetto)
REPO_URL = "https://github.com/django/django.git" 
BRANCH = "main"

# IMPORTANTE: Se non hai PgBouncer attivo sulla 6432, 
# punta direttamente a Postgres (5433) per questo test locale.
# La logica "SingleConnector" funzionerà comunque.
DB_PORT = "6432" 
DB_USER = "sheep_user"
DB_PASS = "sheep_password"
DB_NAME = "sheep_index"
DB_DSN = f"postgresql://{DB_USER}:{DB_PASS}@localhost:{DB_PORT}/{DB_NAME}"

# Setup Logger pulito
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("BENCHMARK")
logger.setLevel(logging.INFO)
# Vediamo i log dell'indexer per seguire il progresso
logging.getLogger("code_graph_indexer").setLevel(logging.INFO)

def clean_database():
    """Pulisce il DB usando un connettore di servizio."""
    print("🧹 Cleaning Database...")
    connector = PooledConnector(dsn=DB_DSN, min_size=1, max_size=1)
    storage = PostgresGraphStorage(connector)
    # Hack veloce per pulire: cancelliamo le repo, cascade cancellerà tutto il resto
    with connector.get_connection() as conn:
        conn.execute("TRUNCATE repositories CASCADE")
    connector.close()

def get_db_stats():
    """Legge le statistiche finali."""
    connector = PooledConnector(dsn=DB_DSN, min_size=1, max_size=1)
    storage = PostgresGraphStorage(connector)
    stats = storage.get_stats()
    connector.close()
    return stats

def run_session(mode_name: str, single_core: bool):
    print(f"\n{'='*60}")
    print(f"🚀 AVVIO SESSIONE: {mode_name}")
    print(f"{'='*60}")
    
    clean_database()
    
    # Impostiamo la variabile d'ambiente che l'Indexer si aspetta
    os.environ["DB_URL"] = DB_DSN
    
    # Disabilitiamo SCIP per misurare la pura velocità del motore Python/DB
    with patch('code_graph_indexer.graph.indexers.scip.SCIPRunner.run_to_disk', return_value=None):
        
        start_time = time.time()
        
        if single_core:
            print("🐌 Single-Core (Simulated via Mock)...")
            # Forziamo cpu_count a restituire 2 (quindi indexer usa max(1, 2-1) = 1 worker)
            with patch('multiprocessing.cpu_count', return_value=2):
                # Istanziamo l'indexer (si auto-configura con os.environ)
                indexer = CodebaseIndexer(REPO_URL, BRANCH)
                indexer.index(force=True)
                indexer.close() # Importante: chiude il pool principale
        else:
            real_cpus = multiprocessing.cpu_count()
            print(f"⚡ Enterprise Multi-Core ({real_cpus} CPU)...")
            
            indexer = CodebaseIndexer(REPO_URL, BRANCH)
            indexer.index(force=True)
            indexer.close()

        duration = time.time() - start_time
        
    stats = get_db_stats()
    return {"mode": mode_name, "duration": duration, "stats": stats}

def print_report(baseline, optimized):
    print("\n\n")
    print("🏆 FINAL BENCHMARK REPORT (Enterprise Architecture)")
    print(f"{'Metrica':<20} | {'Baseline (1 Core)':<20} | {'Enterprise (Multi)':<20} | {'Speedup'}")
    print("-" * 85)
    
    t1, t2 = baseline['duration'], optimized['duration']
    f2 = optimized['stats'].get('files', 0)
    n2 = optimized['stats'].get('total_nodes', 0) # Assicurati di aver aggiornato get_stats in postgres.py!
    
    speedup = t1 / t2 if t2 > 0 else 0
    fps = f2 / t2 if t2 > 0 else 0
    nps = n2 / t2 if t2 > 0 else 0
    
    print(f"{'Tempo (sec)':<20} | {t1:<20.2f} | {t2:<20.2f} | {speedup:.2f}x 🚀")
    print(f"{'File/sec':<20} | {baseline['stats']['files']/t1:<20.2f} | {fps:<20.2f} |")
    print(f"{'Nodi/sec':<20} | {baseline['stats']['total_nodes']/t1:<20.2f} | {nps:<20.2f} |")
    print("-" * 85)
    print(f"✅ Dati Totali (Optimized): {f2} Files, {n2} Nodes.")

if __name__ == "__main__":
    # 1. Baseline Run
    res_base = run_session("Baseline", single_core=True)
    
    # 2. Optimized Run
    res_opt = run_session("Enterprise", single_core=False)
    
    print_report(res_base, res_opt)