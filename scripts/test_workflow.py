import os
import sys
import asyncio
import logging
import shutil
import tempfile
import subprocess
import uuid

# --- ENV ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- SETUP PATH ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, ".."))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from code_graph_indexer.indexer import CodebaseIndexer
from code_graph_indexer.providers.embedding import OpenAIEmbeddingProvider
from code_graph_indexer.storage.connector import PooledConnector

# --- CONFIGURAZIONE ---
DB_DSN = os.getenv("DB_URL", "postgresql://sheep_user:sheep_password@localhost:6432/sheep_index")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

# Logger Setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("REAL_TEST")
logging.getLogger("code_graph_indexer").setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING) 

def create_mini_repo(base_dir: str) -> str:
    """Crea una repo minima per testare OpenAI senza spendere troppo."""
    repo_path = os.path.join(base_dir, "openai-mini-test")
    os.makedirs(repo_path, exist_ok=True)
    
    subprocess.run(["git", "init"], cwd=repo_path, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "ai@test.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "AI"], cwd=repo_path, check=True)
    
    # Un solo file Python con un po' di semantica
    with open(os.path.join(repo_path, "calculator.py"), "w") as f:
        f.write("""
def add(a, b):
    \"\"\"Adds two numbers.\"\"\"
    return a + b

def multiply(a, b):
    \"\"\"Multiplies two numbers.\"\"\"
    return a * b

class AdvancedMath:
    def power(self, base, exp):
        return base ** exp
""")
    
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "Initial"], cwd=repo_path, check=True, stdout=subprocess.DEVNULL)
    # Forza nome branch
    subprocess.run(["git", "branch", "-m", "main"], cwd=repo_path, check=True, stdout=subprocess.DEVNULL)
    
    return repo_path

def clean_db():
    try:
        conn = PooledConnector(DB_DSN, min_size=1, max_size=1)
        with conn.get_connection() as c:
            # Puliamo solo la repo di test specifica se vogliamo, qui tronchiamo per pulizia
            c.execute("TRUNCATE repositories CASCADE")
        conn.close()
    except Exception as e:
        logger.warning(f"DB Warning: {e}")

async def run_real_indexing():
    if not OPENAI_KEY:
        logger.error("❌ ERRORE: OPENAI_API_KEY mancante nelle variabili d'ambiente.")
        return

    tmp_dir = tempfile.mkdtemp()
    
    try:
        clean_db()
        
        logger.info("📂 Creating Mini Repo for OpenAI Test...")
        repo_path = create_mini_repo(tmp_dir)
        repo_url = f"file://{repo_path}"
        
        # 1. INIT INDEXER & PROVIDER
        indexer = CodebaseIndexer(repo_url, "main", db_url=DB_DSN)
        
        # Usiamo text-embedding-3-small (molto economico ed efficiente)
        provider = OpenAIEmbeddingProvider(
            model="text-embedding-3-small", 
            max_concurrency=5
        )
        
        # 2. RUN INDEXING (CPU Phase)
        logger.info("🚀 Phase 1: Parsing & Graph Building...")
        snapshot_id = indexer.index(force=True)
        logger.info(f"✅ Snapshot Created: {snapshot_id}")
        
        # 3. RUN EMBEDDING (Network Phase - REAL OPENAI)
        logger.info("💸 Phase 2: Calling OpenAI API (Async Pipeline)...")
        
        total_vectors = 0
        async for update in indexer.embed(provider, batch_size=10, mock_api=False):
            status = update['status']
            if status == 'embedding_progress':
                # Qui usiamo 'total_embedded' perché è un update parziale
                print(f"   ✨ Embedded {update['total_embedded']} vectors...", end='\r')
            elif status == 'completed':
                # [FIX] Qui usiamo 'newly_embedded' (o recovered) perché è il payload finale
                total_vectors = update.get('newly_embedded', 0) + update.get('recovered_from_history', 0)
                logger.info(f"\n✅ Embedding Completed. Stats: {update}")
        
        if total_vectors == 0:
            logger.warning("⚠️ Zero vectors embedded! Something might be wrong.")
        else:
            logger.info(f"🎉 SUCCESS! {total_vectors} vectors generated/recovered via OpenAI.")

    except Exception as e:
        logger.error(f"❌ Test Failed: {e}", exc_info=True)
    finally:
        shutil.rmtree(tmp_dir)
        if 'indexer' in locals():
            # Questo chiude il pool. Se l'embedder sta ancora girando (es. nel suo finally), 
            # potrebbe causare PoolClosed error. In un test script è accettabile, 
            # in produzione l'app lifecycle è più lungo.
            indexer.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_real_indexing())