import os
os.environ["TOKENIZERS_PARALLELISM"] = "false"
import sys
import shutil
import logging
import time

# --- SETUP PATH ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path: sys.path.insert(0, src_dir)

from code_graph_indexer import CodebaseIndexer, CodeRetriever
from code_graph_indexer.storage.postgres import PostgresGraphStorage
from code_graph_indexer.providers.embedding import FastEmbedProvider

# Configurazione Logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("ADVANCED_TEST")

# --- CONFIGURAZIONE DB ---
# Assicurati che la porta sia quella corretta del tuo Docker (5435)
DB_URL = "postgresql://sheep_user:sheep_password@localhost:5433/sheep_index"

def setup_mixed_repo(path):
    """Crea una repo mista Python/JS/Test per i test."""
    if os.path.exists(path): shutil.rmtree(path)
    os.makedirs(path)
    os.makedirs(os.path.join(path, "src"), exist_ok=True)
    os.makedirs(os.path.join(path, "tests"), exist_ok=True)
    
    # 1. Python File
    with open(os.path.join(path, "src", "main.py"), "w") as f:
        f.write("""
def calculate_tax(amount):
    return amount * 0.22
if __name__ == "__main__":
    print(calculate_tax(100))
""")

    # 2. JavaScript File
    with open(os.path.join(path, "src", "utils.js"), "w") as f:
        f.write("""
function formatCurrency(value) {
    return "$" + value.toFixed(2);
}
""")

    # 3. Test File
    with open(os.path.join(path, "tests", "test_main.py"), "w") as f:
        f.write("def test_tax_calculation(): assert True")

    # [FIX] 4. TSConfig (Fondamentale per scip-typescript)
    # Anche se usiamo solo JS, questo file dice al compilatore come comportarsi
    with open(os.path.join(path, "tsconfig.json"), "w") as f:
        f.write("""
{
  "compilerOptions": {
    "allowJs": true,
    "noEmit": true,
    "target": "esnext",
    "module": "commonjs"
  },
  "include": ["src/**/*"]
}
""")

    # Init Git
    import subprocess
    subprocess.run(["git", "init"], cwd=path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "add", "."], cwd=path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, stdout=subprocess.DEVNULL)
    
def run_test():
    test_repo_path = os.path.abspath("temp_mixed_repo")
    setup_mixed_repo(test_repo_path)
    
    try:
        logger.info(f"🐘 Connecting to Postgres: {DB_URL}")
        # Usa vector_dim=768 per FastEmbed
        storage = PostgresGraphStorage(DB_URL, vector_dim=768)
        provider = FastEmbedProvider()
        indexer = CodebaseIndexer(test_repo_path, storage)
        
        # --- TEST 1: IDEMPOTENZA (Resilienza) ---
        logger.info("\n--- TEST 1: IDEMPOTENZA INDEXING ---")
        
        logger.info("🚀 Round 1: Indexing...")
        indexer.index(force=True)
        # Generiamo embeddings per avere dati completi
        list(indexer.embed(provider))
        
        stats_1 = storage.get_stats()
        nodes_1 = stats_1['total_nodes']
        logger.info(f"📊 Stats Round 1: {nodes_1} nodi.")

        logger.info("🚀 Round 2: Re-Indexing (Force=True)...")
        # Force=True deve cancellare e ricreare pulito
        indexer.index(force=True)
        list(indexer.embed(provider))
        
        stats_2 = storage.get_stats()
        nodes_2 = stats_2['total_nodes']
        logger.info(f"📊 Stats Round 2: {nodes_2} nodi.")
        
        if nodes_1 == nodes_2:
            logger.info("✅ PASS: Il numero di nodi è stabile (Idempotenza garantita).")
        else:
            logger.error(f"❌ FAIL: Duplicazione o perdita dati! {nodes_1} -> {nodes_2}")
            return

        # Recuperiamo Repo ID per le ricerche
        repo_id = indexer.parser.repo_id
        retriever = CodeRetriever(storage, provider)

        # --- TEST 2: FILTRO LINGUAGGIO ---
        logger.info("\n--- TEST 2: MULTI-LANGUAGE FILTERING ---")
        
        # Caso A: Cerchiamo logica generica in Python
        logger.info("🔎 Searching 'logic' in PYTHON...")
        res_py = retriever.retrieve("logic", repo_id, filters={"language": "python"})
        
        # Verifiche
        has_py = any("main.py" in r.file_path for r in res_py)
        has_js = any(".js" in r.file_path for r in res_py)
        
        if has_py and not has_js:
            logger.info("✅ PASS: Trovato solo Python.")
        else:
            logger.error(f"❌ FAIL: Filtro Python non rispettato. (Has Py: {has_py}, Has JS: {has_js})")

        # Caso B: Cerchiamo logica generica in JS
        logger.info("🔎 Searching 'logic' in JAVASCRIPT...")
        res_js = retriever.retrieve("logic", repo_id, filters={"language": "javascript"})
        
        has_py_in_js = any(".py" in r.file_path for r in res_js)
        has_js_in_js = any("utils.js" in r.file_path for r in res_js)
        
        if has_js_in_js and not has_py_in_js:
            logger.info("✅ PASS: Trovato solo JavaScript.")
        else:
            logger.error(f"❌ FAIL: Filtro JS non rispettato.")

        # --- TEST 3: ESCLUSIONE CATEGORIA ---
        logger.info("\n--- TEST 3: CATEGORY EXCLUSION ---")
        # Cerchiamo "test" che è presente sia nel nome del file di test che nel contenuto
        # Ma chiediamo di escludere la categoria 'test'
        res_no_test = retriever.retrieve("test calculation", repo_id, filters={"exclude_category": "test"})
        
        found_test_file = any("tests/test_main.py" in r.file_path for r in res_no_test)
        
        if not found_test_file:
            logger.info("✅ PASS: File di test correttamente esclusi.")
        else:
            logger.error("❌ FAIL: Trovato file di test nonostante il filtro.")

        logger.info("\n🎉 TUTTI I TEST AVANZATI COMPLETATI!")

    except Exception as e:
        logger.error(f"❌ ERRORE CRITICO: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if 'storage' in locals(): storage.close()
        if os.path.exists(test_repo_path): shutil.rmtree(test_repo_path)

if __name__ == "__main__":
    run_test()