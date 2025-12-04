import os
import shutil
import subprocess
import tempfile
import logging
import time
from typing import Optional

# Assicuriamoci che i path siano corretti per l'import
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from code_graph_indexer import CodebaseIndexer
from code_graph_indexer.storage.postgres import PostgresGraphStorage
# Se hai un provider mock per i test usalo, altrimenti usa quello reale
try:
    from code_graph_indexer.providers.openai_emb import OpenAIEmbeddingProvider
except ImportError:
    from code_graph_indexer.providers.embedding import OpenAIEmbeddingProvider

# Configurazione Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# CONFIGURAZIONE
REPO_URL = "https://github.com/pallets/flask.git"
DB_URL = "postgresql://sheep_user:sheep_password@localhost:5433/sheep_index" # <--- ADATTA ALLA TUA CONFIG
CLEANUP_ON_EXIT = True

def clone_repo(url: str, target_dir: str):
    """
    Esegue un clone 'shallow' (depth 1) per risparmiare tempo e banda.
    Simula una situazione 'bare' nel senso di 'repository pulito appena scaricato'.
    """
    logger.info(f"🔄 Cloning {url} into {target_dir}...")
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", "main", url, target_dir],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        logger.info("✅ Clone completato.")
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Errore durante il clone: {e.stderr.decode()}")
        raise

def run_test():
    # 1. Setup Ambiente Temporaneo
    temp_dir = tempfile.mkdtemp(prefix="indexer_test_flask_")
    
    try:
        # 2. Clone del Progetto Reale
        clone_repo(REPO_URL, temp_dir)
        
        # 3. Inizializzazione Storage
        logger.info(f"🐘 Connecting to DB: {DB_URL}")
        storage = PostgresGraphStorage(DB_URL)
        
        # 4. Inizializzazione Indexer
        logger.info("⚙️ Initializing CodebaseIndexer...")
        indexer = CodebaseIndexer(temp_dir, storage)
        
        # 5. Esecuzione Indexing (Parsing + Graph Build)
        start_time = time.time()
        indexer.index(force=True)
        parsing_time = time.time() - start_time
        
        # 6. Verifica Statistiche (Il vero 'Test')
        stats = storage.get_stats()
        print("\n" + "="*40)
        print(f"📊 REPORT INDEXING: Flask")
        print("="*40)
        print(f"⏱️  Tempo Parsing:    {parsing_time:.2f}s")
        print(f"📂 File Processati:  {stats.get('files', 0)}")
        print(f"🧩 Nodi (Chunks):    {stats.get('total_nodes', 0)}")
        print(f"🔗 Archi (Edges):    {stats.get('total_edges', 'N/A')}") # Se il metodo get_stats lo supporta
        print("="*40 + "\n")
        
        # Assertion minime per considerare il test passato
        assert stats['files'] > 20, "Troppi pochi file indicizzati per Flask!"
        assert stats['total_nodes'] > 100, "Troppi pochi nodi trovati!"
        
        logger.info("🟢 TEST PASSATO: Indexing strutturale completato con successo.")

        # 7. (Opzionale) Test Embedding
        # Se hai le API Key settate, puoi scommentare questo blocco per testare anche la parte vettoriale
        """
        if os.getenv("OPENAI_API_KEY"):
            logger.info("🤖 Avvio Embedding Test (Batch ridotto)...")
            provider = OpenAIEmbeddingProvider(model="text-embedding-3-small")
            # Embeddiamo solo un piccolo batch per verificare che non crashi
            list(indexer.embed(provider, batch_size=10)) 
            logger.info("✅ Embedding smoke test superato.")
        """

    except Exception as e:
        logger.error(f"🔴 TEST FALLITO: {e}")
        raise
    finally:
        # 8. Cleanup
        if CLEANUP_ON_EXIT:
            logger.info(f"🧹 Pulizia directory temporanea: {temp_dir}")
            shutil.rmtree(temp_dir)
            if 'storage' in locals():
                storage.close()

if __name__ == "__main__":
    run_test()