import os
os.environ["TOKENIZERS_PARALLELISM"] = "false"
import sys
import shutil
import logging
import time
import concurrent.futures
import threading

# --- SETUP PATH ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path: sys.path.insert(0, src_dir)

from code_graph_indexer import CodebaseIndexer, CodeRetriever
from code_graph_indexer.storage.postgres import PostgresGraphStorage
from code_graph_indexer.providers.embedding import FastEmbedProvider

# Configurazione Logging (Thread-safe format)
logging.basicConfig(
    level=logging.INFO, 
    format='[%(threadName)s] %(asctime)s | %(message)s', 
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("CONCURRENT_TEST")

# --- CONFIGURAZIONE DB ---
# Assicurati che la porta sia quella corretta (5435 o 5433)
DB_URL = "postgresql://sheep_user:sheep_password@localhost:5433/sheep_index"

def setup_repo(path, name, unique_function):
    """Crea una repo dummy con un contenuto unico."""
    if os.path.exists(path): shutil.rmtree(path)
    os.makedirs(path)
    os.makedirs(os.path.join(path, "src"), exist_ok=True)
    
    with open(os.path.join(path, "src", "main.py"), "w") as f:
        f.write(f"""
def process_{name}_transaction(data):
    # Questa stringa è univoca per la repo {name}
    print("Processing {unique_function}...")
    return True
""")
    
    import subprocess
    subprocess.run(["git", "init"], cwd=path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "add", "."], cwd=path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", f"init {name}"], cwd=path, stdout=subprocess.DEVNULL)
    # URL remoto finto per generare ID diversi
    subprocess.run(["git", "remote", "add", "origin", f"https://github.com/fake/repo-{name}.git"], cwd=path, stdout=subprocess.DEVNULL)

def worker_pipeline(storage, provider, repo_path, repo_name, unique_keyword):
    """
    Funzione eseguita da ogni thread: Index -> Embed -> Check Isolation.
    """
    thread_name = threading.current_thread().name
    logger.info(f"🚀 START Pipeline per {repo_name}")
    
    try:
        # 1. INDEXING
        indexer = CodebaseIndexer(repo_path, storage)
        indexer.index(force=True)
        repo_id = indexer.parser.repo_id
        logger.info(f"✅ Indexing OK. ID: {repo_id}")

        # 2. EMBEDDING
        list(indexer.embed(provider))
        logger.info(f"✅ Embedding OK.")

        # 3. RETRIEVAL (Verifica Isolamento Locale)
        retriever = CodeRetriever(storage, provider)
        
        # A. Positive Check: Devo trovare la mia keyword
        logger.info(f"🔎 Searching '{unique_keyword}' in {repo_name}...")
        results = retriever.retrieve(unique_keyword, repo_id, limit=5)
        
        if not results or not any(unique_keyword in r.content for r in results):
            logger.error(f"❌ FAIL {repo_name}: Non trovo i miei dati!")
            return False
            
        # B. Cross-Contamination Check: Non devo trovare roba dell'altro repo
        # (Simuliamo cercando una keyword generica 'Processing' e filtrando per questo repo)
        generic_results = retriever.retrieve("Processing", repo_id, limit=10)
        
        # Costruiamo il nome della funzione che CI ASPETTIAMO di trovare (es. process_A_transaction)
        expected_func = f"process_{repo_name}_transaction"
        
        for r in generic_results:
            content = r.content
            # Se trovo una funzione "process_..."
            if "def process_" in content:
                # Se NON è la MIA funzione, allora è un leak (ho trovato B in A)
                if expected_func not in content:
                    logger.error(f"❌ FAIL {repo_name}: LEAK RILEVATO! Trovato codice alieno: {content[:50]}...")
                    return False
                # Se È la mia funzione, tutto ok.

        logger.info(f"🎉 SUCCESS {repo_name}: Pipeline completata e isolata.")
        return True

    except Exception as e:
        logger.error(f"🔥 CRASH {repo_name}: {e}")
        import traceback
        traceback.print_exc()
        return False

def run_concurrent_test():
    path_a = os.path.abspath("temp_concurrent_a")
    path_b = os.path.abspath("temp_concurrent_b")
    
    # [FIX] Usiamo nomi coerenti: "A" e "B" sia per setup che per worker
    setup_repo(path_a, "A", "payment_gateway_v1")
    setup_repo(path_a, "B", "order_fulfillment_v2")
    
    # Storage Condiviso (Unico Connection Pool per tutti i thread)
    # vector_dim=768 se usi FastEmbed
    logger.info(f"🐘 Connecting to Shared Storage...")
    storage = PostgresGraphStorage(DB_URL, min_size=4, max_size=10, vector_dim=768)
    
    provider = FastEmbedProvider()
    
    try:
        logger.info("\n⚡ AVVIO TEST PARALLELO (2 Threads) ⚡")
        start_time = time.time()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            # Lanciamo i due worker contemporaneamente con i nomi CORRETTI ("A", "B")
            future_a = executor.submit(worker_pipeline, storage, provider, path_a, "A", "payment_gateway_v1")
            future_b = executor.submit(worker_pipeline, storage, provider, path_b, "B", "order_fulfillment_v2")
            
            success_a = future_a.result()
            success_b = future_b.result()
            
        duration = time.time() - start_time
        
        logger.info("-" * 40)
        if success_a and success_b:
            logger.info(f"🏆 TEST PARALLELO SUPERATO in {duration:.2f}s!")
            logger.info("   - Pool Postgres ha gestito la concorrenza.")
            logger.info("   - Isolamento dati mantenuto sotto carico.")
        else:
            logger.error("💥 TEST FALLITO. Controlla i log sopra.")

    finally:
        storage.close()
        if os.path.exists(path_a): shutil.rmtree(path_a)
        if os.path.exists(path_b): shutil.rmtree(path_b)

if __name__ == "__main__":
    run_concurrent_test()