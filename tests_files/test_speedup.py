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

from opentelemetry import trace
from opentelemetry.trace import ProxyTracerProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource


# [TELEMETRY HOOK]
# Questa funzione è Top-Level, quindi è serializzabile (Picklable) per i worker.
def setup_telemetry():
    # 1. Definisci chi siamo
    resource = Resource.create(attributes={
        "service.name": "sheep-indexer-worker",
        "deployment.environment": "development",
        "process.pid": os.getpid()
    })

    # 2. Configura il Provider
    # [FIX] Controllo robusto invece di _sdk_config
    provider = trace.get_tracer_provider()
    
    # Se il provider è ancora un Proxy, significa che non è stato configurato.
    if isinstance(provider, ProxyTracerProvider):
        print(f"📡 [PID {os.getpid()}] Configuring OTel Exporter...")
        
        real_provider = TracerProvider(resource=resource)
        
        # 3. Configura l'Exporter
        exporter = OTLPSpanExporter(endpoint="http://localhost:4317", insecure=True)
        real_provider.add_span_processor(BatchSpanProcessor(exporter))
        
        # 4. Imposta come Globale
        trace.set_tracer_provider(real_provider)
    else:
        print(f"ℹ️  [PID {os.getpid()}] OTel already configured.")
# --- CONFIGURAZIONE ---
REPO_URL = "https://github.com/django/django.git" 
BRANCH = "main"

# DB Config
DB_PORT = "6432" 
DB_USER = "sheep_user"
DB_PASS = "sheep_password"
DB_NAME = "sheep_index"
DB_DSN = f"postgresql://{DB_USER}:{DB_PASS}@localhost:{DB_PORT}/{DB_NAME}"

# Logger Setup
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("BENCHMARK")
logger.setLevel(logging.INFO)
logging.getLogger("code_graph_indexer").setLevel(logging.INFO)

def clean_database():
    """Pulisce il DB usando un connettore di servizio."""
    print("🧹 Cleaning Database...")
    try:
        connector = PooledConnector(dsn=DB_DSN, min_size=1, max_size=1)
        # Non serve istanziare storage wrapper, basta una query raw
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
    
    os.environ["DB_URL"] = DB_DSN
    
    start_time = time.time()
    
    if single_core:
        print("🐌 Single-Core (Simulated)...")
        # Simuliamo pochi core per vedere la differenza
        with patch('multiprocessing.cpu_count', return_value=2):
            # [MODIFICA QUI] Passiamo l'hook di telemetria
            indexer = CodebaseIndexer(
                REPO_URL, 
                BRANCH, 
                worker_telemetry_init=setup_telemetry
            )
            indexer.index(force=True)
            indexer.close()
    else:
        real_cpus = multiprocessing.cpu_count()
        print(f"⚡ Enterprise Multi-Core ({real_cpus} CPU)...")
        
        # [MODIFICA QUI] Passiamo l'hook di telemetria
        indexer = CodebaseIndexer(
            REPO_URL, 
            BRANCH, 
            worker_telemetry_init=setup_telemetry
        )
        indexer.index(force=True)
        indexer.close()

    duration = time.time() - start_time
    
    stats = get_db_stats()
    return {"mode": mode_name, "duration": duration, "stats": stats}

def print_report(optimized):
    print("\n\n")
    print("🏆 FINAL BENCHMARK REPORT (Full Stack SCIP)")
    print(f"{'Metrica':<20} | {'Baseline (Ref)':<20} | {'Enterprise (Multi)':<20} | {'Speedup'}")
    print("-" * 85)
    
    # Valore di riferimento hardcoded per confronto rapido
    t1 = 241.69 
    t2 = optimized['duration']
    
    n1 = 40796 # Baseline reference nodes
    
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
    else:
        print(f"✅ Integrità Dati OK: {n2} nodi.")

if __name__ == "__main__":
    setup_telemetry()
    
    try:
        # Esegui il benchmark
        res_opt = run_session("Enterprise", single_core=False)
        print_report(res_opt)
        
    finally:
        # [FIX CRITICO] Forza l'invio dei dati pendenti
        print("📡 Flushing telemetry to Jaeger...")
        trace.get_tracer_provider().shutdown()
        print("👋 Done.")