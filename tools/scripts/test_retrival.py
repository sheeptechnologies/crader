import logging
import os
import shutil
import sys
import tempfile

# --- SETUP PATH ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

try:
    from crader import CodebaseIndexer, CodeRetriever
    from crader.providers.embedding import FastEmbedProvider
except ImportError as e:
    print(f"❌ Errore importazione: {e}")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger("TEST")

def create_dummy_repo(base_path: str) -> str:
    repo_path = os.path.join(base_path, "dummy-shop-repo")
    os.makedirs(repo_path, exist_ok=True)
    src_path = os.path.join(repo_path, "src")
    os.makedirs(src_path)

    with open(os.path.join(src_path, "db.py"), "w") as f:
        f.write("""
class DatabaseManager:
    def connect(self): print("Connecting...")
""")

    # Init git rapido
    import subprocess
    subprocess.run(["git", "init"], cwd=repo_path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=repo_path, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_path)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path)
    subprocess.run(["git", "add", "."], cwd=repo_path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_path, stdout=subprocess.DEVNULL)

    return repo_path

def test_retrieval_pipeline():
    temp_dir = tempfile.mkdtemp()
    try:
        repo_path = create_dummy_repo(temp_dir)
        db_path = os.path.join(temp_dir, "test.db")

        # 1. Indexing
        indexer = CodebaseIndexer(repo_path, db_path=db_path)
        indexer.index()

        # RECUPERIAMO IL REPO ID REALE (Fondamentale!)
        real_repo_id = indexer.parser.repo_id
        logger.info(f"🔑 Repo ID Reale: {real_repo_id}")

        # 2. Embedding
        provider = FastEmbedProvider(model_name="jinaai/jina-embeddings-v2-base-code")
        # Consuma il generatore
        list(indexer.embed(provider, batch_size=10))

        # 3. Retrieval Setup
        retriever = CodeRetriever(indexer.storage, provider)
        query = "database connection"

        # --- TEST 1: Ricerca Corretta (Happy Path) ---
        logger.info("\n🧪 TEST 1: Ricerca con Repo ID corretto")
        results = retriever.retrieve(query, repo_id=real_repo_id, limit=5)
        if results:
            logger.info(f"✅ OK: Trovati {len(results)} risultati.")
        else:
            logger.error("❌ FAIL: Nessun risultato trovato!")

        # --- TEST 2: Repo Isolation (Security) ---
        logger.info("\n🧪 TEST 2: Ricerca con Repo ID errato (Isolation)")
        fake_id = "deadbeef" * 8
        results_fake = retriever.retrieve(query, repo_id=fake_id, limit=5)
        if len(results_fake) == 0:
            logger.info("✅ OK: L'isolamento funziona (0 risultati).")
        else:
            logger.error(f"❌ FAIL: Leak di dati! Trovati {len(results_fake)} risultati da altra repo.")

        # --- TEST 3: Branch Filtering ---
        logger.info("\n🧪 TEST 3: Branch Filtering")
        # Cerca su un branch che non esiste
        results_branch = retriever.retrieve(query, repo_id=real_repo_id, branch="dev-pre-alpha")
        if len(results_branch) == 0:
            logger.info("✅ OK: Filtro branch funziona (0 risultati).")
        else:
            logger.error("❌ FAIL: Il filtro branch non funziona.")

        # --- TEST 4: Enforcement (Safety) ---
        logger.info("\n🧪 TEST 4: Repo ID Mancante (Safety)")
        try:
            retriever.retrieve(query, repo_id=None)
            logger.error("❌ FAIL: Ha permesso la ricerca senza Repo ID!")
        except ValueError:
            logger.info("✅ OK: Ha sollevato errore come previsto.")

    except Exception as e:
        logger.error(f"CRASH: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if 'indexer' in locals():
            indexer.close()
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    test_retrieval_pipeline()
