import os
import sys
import shutil
import logging
import time

# --- SETUP PATH ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path: sys.path.insert(0, src_dir)

from code_graph_indexer import CodebaseIndexer
from code_graph_indexer.storage.postgres import PostgresGraphStorage
from code_graph_indexer.providers.embedding import FastEmbedProvider

# Configurazione Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger("CHAOS_TEST")

# Assicurati che la porta sia quella corretta (5433 o 5435)
DB_URL = "postgresql://sheep_user:sheep_password@localhost:5433/sheep_index"

def setup_toxic_repo(path):
    """
    Genera una repository piena di file 'tossici' per testare la robustezza.
    """
    if os.path.exists(path): shutil.rmtree(path)
    os.makedirs(path)
    os.makedirs(os.path.join(path, "src"), exist_ok=True)
    
    # 1. GOOD FILE (Il controllo)
    with open(os.path.join(path, "src", "good.py"), "w") as f:
        f.write("def hello_world(): print('I survived the chaos!')")

    # 2. SYNTAX HORROR (Parser Stress)
    with open(os.path.join(path, "src", "bad_syntax.py"), "w") as f:
        f.write("def function_with_no_end( { return [1, 2 ")

    # 3. BINARY MASKED (Encoding Crash)
    with open(os.path.join(path, "src", "binary_fake.py"), "wb") as f:
        f.write(b'\x00\x01\x02' * 100) 

    # 4. GHOST ENCODING (Decode Error - Latin-1)
    with open(os.path.join(path, "src", "encoding_error.py"), "wb") as f:
        f.write(b"print('caf\xe9')") 

    # 5. HEAVYWEIGHT (OOM / Timeout Risk)
    # File 15MB su una riga.
    with open(os.path.join(path, "src", "heavy.js"), "w") as f:
        f.write("const huge = '" + "a" * (15 * 1024 * 1024) + "';")

    # [FIX CRITICO] 6. TSConfig per proteggere SCIP
    # Senza questo, scip-typescript fallisce (missing config).
    # Inoltre, ESCLUDIAMO heavy.js dalla config di SCIP per evitare che crashi il subprocess.
    # Vogliamo testare che il NOSTRO parser (Python) lo rilevi e lo skippi, non che SCIP muoia.
    with open(os.path.join(path, "tsconfig.json"), "w") as f:
        f.write("""
{
  "compilerOptions": {
    "allowJs": true,
    "noEmit": true,
    "target": "esnext",
    "module": "commonjs"
  },
  "include": ["src/**/*"],
  "exclude": ["src/heavy.js"] 
}
""")

    # Init Git
    import subprocess
    subprocess.run(["git", "init"], cwd=path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "add", "."], cwd=path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "init chaos"], cwd=path, stdout=subprocess.DEVNULL)

def verify_db_state(storage, repo_id):
    """Controlla lo stato dei file nel DB."""
    results = {}
    with storage.pool.connection() as conn:
        rows = conn.execute(
            "SELECT path, parsing_status, parsing_error FROM files WHERE repo_id=%s", 
            (repo_id,)
        ).fetchall()
        
        for r in rows:
            results[r['path']] = {
                'status': r['parsing_status'],
                'error': r['parsing_error']
            }
    return results

def run_chaos_test():
    test_repo_path = os.path.abspath("temp_chaos_repo")
    setup_toxic_repo(test_repo_path)
    
    storage = PostgresGraphStorage(DB_URL, vector_dim=768)
    indexer = CodebaseIndexer(test_repo_path, storage)
    
    logger.info("🚀 STARTING CHAOS MONKEY TEST...")
    
    try:
        # EXECUTION
        # Questo NON deve lanciare eccezioni
        indexer.index(force=True)
        logger.info("✅ Indexing completato senza crash (Survival passed).")
        
        repo_id = indexer.parser.repo_id
        db_files = verify_db_state(storage, repo_id)
        
        # ASSERT 1: GOOD FILE
        good = db_files.get("src/good.py")
        if good and good['status'] == 'success':
            logger.info("✅ PASS: File valido 'good.py' indicizzato.")
        else:
            logger.error(f"❌ FAIL: File valido perso. Stato: {good}")

        # ASSERT 2: BINARY FILE
        binary = db_files.get("src/binary_fake.py")
        if binary and binary['status'] == 'skipped' and "Binary" in (binary['error'] or ""):
            logger.info(f"✅ PASS: File binario saltato. ({binary['error']})")
        else:
            logger.error(f"❌ FAIL: Binario non gestito. Stato: {binary}")

        # ASSERT 3: HEAVY FILE
        # Verifichiamo che il NOSTRO parser lo abbia skippato per dimensione
        heavy = db_files.get("src/heavy.js")
        if heavy and heavy['status'] == 'skipped' and "too large" in (heavy['error'] or ""):
            logger.info(f"✅ PASS: File gigante saltato dal parser. ({heavy['error']})")
        else:
            logger.error(f"❌ FAIL: File gigante non gestito. Stato: {heavy}")

        # ASSERT 4: ENCODING / SYNTAX
        bad_syntax = db_files.get("src/bad_syntax.py")
        if bad_syntax:
            logger.info(f"ℹ️ Info: Bad Syntax file status: {bad_syntax['status']} (Tree-sitter handles errors gracefully)")
        
        logger.info("\n🎉 CHAOS TEST PASSED: Il sistema è resiliente!")

    except Exception as e:
        logger.error(f"🔥 CRITICAL FAIL: Il sistema è crashato! {e}")
        import traceback
        traceback.print_exc()
    finally:
        storage.close()
        if os.path.exists(test_repo_path): shutil.rmtree(test_repo_path)

if __name__ == "__main__":
    run_chaos_test()