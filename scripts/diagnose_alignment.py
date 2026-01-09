import os
os.environ["TOKENIZERS_PARALLELISM"] = "false"
import sys
import shutil
import logging
import time
from typing import List, Dict, Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- SETUP PATH ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path: sys.path.insert(0, src_dir)

from code_graph_indexer import CodebaseIndexer, CodeRetriever
from code_graph_indexer.storage.postgres import PostgresGraphStorage
from code_graph_indexer.providers.embedding import FastEmbedProvider,OpenAIEmbeddingProvider

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger("RIGOROUS")

# --- CONFIGURAZIONE ---
# Usa la porta 5433 come da tuo snippet (Verifica con 'docker-compose ps' se è 5433 o 5435)
DB_URL = "postgresql://sheep_user:sheep_password@localhost:5433/sheep_index"
REPO_DIR = os.path.abspath("temp_rigorous_repo")

def setup_complex_repo(path):
    """Crea una repo complessa per testare tutti i casi d'uso semantici."""
    if os.path.exists(path): shutil.rmtree(path)
    os.makedirs(path)
    os.makedirs(os.path.join(path, "src", "backend"), exist_ok=True)
    os.makedirs(os.path.join(path, "src", "frontend"), exist_ok=True)
    os.makedirs(os.path.join(path, "tests"), exist_ok=True)
    
    # 1. Python Logic (Backend) - Entry Point & Function
    with open(os.path.join(path, "src", "backend", "server.py"), "w") as f:
        f.write("""
# Questo è un entry point
if __name__ == "__main__":
    print("Server starting...")

def process_payment(amount):
    return amount * 1.2
""")

    # 2. Python API (Backend) - API Endpoint
    with open(os.path.join(path, "src", "backend", "api.py"), "w") as f:
        f.write("""
# Questo è un endpoint (simulato con decoratore)
@app.get("/users")
def get_users():
    return []
""")

    # 3. JavaScript Logic (Frontend)
    with open(os.path.join(path, "src", "frontend", "utils.js"), "w") as f:
        f.write("""
function validateEmail(email) {
    return email.includes('@');
}
""")

    # 4. JSON Config (Category: Config)
    with open(os.path.join(path, "config.json"), "w") as f:
        f.write('{"env": "production", "debug": false}')

    # 5. Test File (Category: Test)
    with open(os.path.join(path, "tests", "test_server.py"), "w") as f:
        f.write("""
class TestServer(unittest.TestCase):
    def test_payment(self):
        assert True
""")

    # 6. TSConfig (Vitale per SCIP JS/TS)
    with open(os.path.join(path, "tsconfig.json"), "w") as f:
        f.write('{"compilerOptions": {"allowJs": true}, "include": ["src/**/*"]}')

    # Init Git
    import subprocess
    subprocess.run(["git", "init"], cwd=path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "add", "."], cwd=path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, stdout=subprocess.DEVNULL)

def assert_retrieval(retriever, repo_id, name, query, filters, expect_files=[], forbid_files=[], expect_tags=[]):
    """Helper per eseguire assert sui risultati di ricerca."""
    print(f"\n🧪 TEST: {name}")
    print(f"   Query: '{query}' | Filters: {filters}")
    
    try:
        results = retriever.retrieve(query, repo_id, limit=10, strategy="hybrid", filters=filters)
    except Exception as e:
        logger.error(f"❌ CRASH DURING QUERY: {e}")
        raise e

    found_files = [r.file_path for r in results]
    found_labels = [l for r in results for l in r.semantic_labels]
    
    # Check Files Expected
    for f in expect_files:
        if not any(f in path for path in found_files):
            logger.error(f"❌ FAIL: File atteso '{f}' NON trovato. Trovati: {found_files}")
            return False
            
    # Check Files Forbidden
    for f in forbid_files:
        if any(f in path for path in found_files):
            logger.error(f"❌ FAIL: File proibito '{f}' TROVATO! (Il filtro non funziona).")
            return False

    # Check Tags Expected
    for tag in expect_tags:
        # Cerchiamo parzialmente nel testo delle label
        if not any(tag.lower() in l.lower() for l in found_labels):
            logger.error(f"❌ FAIL: Tag semantico '{tag}' mancante nei risultati.")
            return False

    logger.info("✅ PASS")
    return True

def run_rigorous_test():
    setup_complex_repo(REPO_DIR)
    
    try:
        logger.info(f"🐘 Connecting to DB...")

        storage = PostgresGraphStorage(DB_URL, vector_dim=1536)
        provider = OpenAIEmbeddingProvider(model="text-embedding-3-small")
        indexer = CodebaseIndexer(REPO_DIR, storage)
        
        # --- 1. IDEMPOTENZA & STABILITA' ---
        logger.info("🚀 Round 1 Indexing...")
        indexer.index(force=True)
        list(indexer.embed(provider)) # Consuma generatore
        
        stats1 = storage.get_stats()
        logger.info(f"📊 Stats 1: {stats1}")

        logger.info("🚀 Round 2 Indexing (Check Duplicati)...")
        indexer.index(force=True)
        list(indexer.embed(provider))
        
        stats2 = storage.get_stats()
        if stats1['total_nodes'] != stats2['total_nodes']:
            raise AssertionError(f"❌ FAIL Idempotenza: Nodi cambiati {stats1['total_nodes']} -> {stats2['total_nodes']}")
        if stats1['embeddings'] != stats2['embeddings']:
            raise AssertionError(f"❌ FAIL Idempotenza: Embeddings cambiati {stats1['embeddings']} -> {stats2['embeddings']}")
        logger.info("✅ PASS: Idempotenza confermata.")

        repo_id = indexer.parser.repo_id
        retriever = CodeRetriever(storage, provider)

        # --- 2. FILTRI MULTI-LINGUA (Liste) ---
        # Testiamo che la clausola SQL 'ANY(%s)' funzioni correttamente
        assert_retrieval(
            retriever, repo_id, "Filter Python Only",
            query="server logic",
            filters={"language": ["python"]}, # Passiamo LISTA
            expect_files=["server.py"],
            forbid_files=["utils.js", "config.json"]
        )

        assert_retrieval(
            retriever, repo_id, "Filter JS Only",
            query="logic",
            filters={"language": ["javascript"]}, 
            expect_files=["utils.js"],
            forbid_files=["server.py"]
        )

        # --- 3. ESCLUSIONE CATEGORIA (Ibrido File/Chunk) ---
        # Deve escludere sia 'test_server.py' (file category) 
        # sia 'config.json' (file category) se richiesto
        assert_retrieval(
            retriever, repo_id, "Exclude Test & Config",
            query="server test configuration",
            filters={"exclude_category": ["test", "config"]}, # LISTA Multipla
            expect_files=["server.py"],
            forbid_files=["test_server.py", "config.json"]
        )

        # --- 4. FILTRI SEMANTICI (Role) ---
        # Deve trovare l'endpoint grazie al tag @role.api_endpoint
        assert_retrieval(
            retriever, repo_id, "Find API Endpoint",
            query="users endpoint",
            filters={"role": ["api_endpoint"]},
            expect_files=["api.py"],
            expect_tags=["API Route Handler"]
        )

        # Deve trovare l'entry point
        assert_retrieval(
            retriever, repo_id, "Find Entry Point",
            query="start application",
            filters={"role": ["entry_point"]},
            expect_files=["server.py"],
            expect_tags=["Entry Point"]
        )

        # --- 5. FILTRI COMPLESSI (Mix) ---
        # "Cerca in backend/ O frontend/, ma solo Python, e niente test"
        assert_retrieval(
            retriever, repo_id, "Complex Query",
            query="logic",
            filters={
                "path_prefix": ["src/backend", "src/frontend"],
                "language": ["python"],
                "exclude_category": ["test"]
            },
            expect_files=["server.py"],     # Python in backend -> OK
            forbid_files=["utils.js",       # JS -> No
                          "test_server.py"] # Test -> No
        )

        logger.info("\n🎉 TUTTI I TEST RIGOROSI COMPLETATI CON SUCCESSO!")

    except Exception as e:
        logger.error(f"🔥 TEST FALLITO: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if 'storage' in locals(): storage.close()
        if os.path.exists(REPO_DIR): shutil.rmtree(REPO_DIR)

if __name__ == "__main__":
    run_rigorous_test()