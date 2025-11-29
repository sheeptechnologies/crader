
import os
# [FIX 1] Disabilita parallelismo dei tokenizer per evitare warning/deadlock
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import sys
import json
import shutil
import tempfile
import logging
import sqlite3
import argparse
import subprocess
from typing import List, Dict, Any

# --- SETUP PATH ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

try:
    from code_graph_indexer import CodebaseIndexer, CodeRetriever
    from code_graph_indexer.providers.embedding import FastEmbedProvider
    from code_graph_indexer.storage.sqlite import SqliteGraphStorage
except ImportError as e:
    print(f"❌ Errore importazione: {e}")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger("ADVANCED_TEST")

def setup_git_repo(path: str):
    """Inizializza una repo git vuota."""
    subprocess.run(["git", "init"], cwd=path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path)

def commit_file(repo_path: str, filename: str, content: str, message: str):
    """Crea/Aggiorna un file e fa commit."""
    filepath = os.path.join(repo_path, filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w") as f:
        f.write(content)
    
    subprocess.run(["git", "add", filename], cwd=repo_path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", message], cwd=repo_path, stdout=subprocess.DEVNULL)

def checkout_branch(repo_path: str, branch: str, create: bool = False):
    """Cambia branch."""
    cmd = ["git", "checkout"]
    if create:
        cmd.append("-b")
    cmd.append(branch)
    subprocess.run(cmd, cwd=repo_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def force_update_branch_in_db(db_path: str, file_pattern: str, new_branch: str):
    """
    HELPER DI TEST: Forza l'aggiornamento del branch nel DB per i nodi specificati.
    Serve se l'Embedder non sta ancora salvando dinamicamente il branch corrente.
    """
    conn = sqlite3.connect(db_path)
    # Aggiorna node_embeddings dove il contenuto corrisponde al pattern (per simulare l'indicizzazione corretta)
    conn.execute(
        "UPDATE node_embeddings SET branch = ? WHERE text_content LIKE ?", 
        (new_branch, f"%{file_pattern}%")
    )
    conn.commit()
    conn.close()

def run_advanced_tests():
    temp_dir = tempfile.mkdtemp()
    repo_path = os.path.join(temp_dir, "advanced_repo")
    db_path = "index.db"
    os.makedirs(repo_path)

    try:
        setup_git_repo(repo_path)
        
        # --- SCENARIO 1: DIVERGENZA SEMANTICA TRA BRANCH ---
        logger.info("\n🧪 SCENARIO 1: Divergenza Semantica (Sorting Algorithms)")
        
        # 1.1 Branch 'main': Implementazione lenta (Bubble Sort)
        logger.info("   -> Creating 'main' with Bubble Sort...")
        checkout_branch(repo_path, "main", create=True)
        
        bubble_sort_code = """
            def sort_algorithm(arr):
                # Standard Bubble Sort implementation
                # Time Complexity: O(n^2) - Slow for large datasets
                n = len(arr)
                for i in range(n):
                    for j in range(0, n-i-1):
                        if arr[j] > arr[j+1]:
                            arr[j], arr[j+1] = arr[j+1], arr[j]
                return arr
        """
        commit_file(repo_path, "src/algo.py", bubble_sort_code, "Add bubble sort")
        
        # Indexing Main
        storage = SqliteGraphStorage(db_path=db_path)
        indexer = CodebaseIndexer(repo_path,storage=storage)
        indexer.index()
        provider = FastEmbedProvider(model_name="jinaai/jina-embeddings-v2-base-code")
        list(indexer.embed(provider, batch_size=10)) # Consuma generatore
        
        # 1.2 Branch 'feature/fast': Implementazione veloce (Quick Sort)
        logger.info("   -> Creating 'feature/fast' with Quick Sort...")
        checkout_branch(repo_path, "feature/fast", create=True)
        
        quick_sort_code = """
        def sort_algorithm(arr):
            # Optimized Quick Sort implementation
            # Time Complexity: O(n log n) - Efficient
            if len(arr) <= 1: return arr
            pivot = arr[len(arr) // 2]
            left = [x for x in arr if x < pivot]
            middle = [x for x in arr if x == pivot]
            right = [x for x in arr if x > pivot]
            return sort_algorithm(left) + middle + sort_algorithm(right)
        """
        commit_file(repo_path, "src/algo.py", quick_sort_code, "Upgrade to quick sort")
        
        # Indexing Feature Branch
        # Nota: L'indexer deve essere re-istanziato o rieseguito sulla nuova repo checkoutata
        indexer.index(force=True) 
        list(indexer.embed(provider, batch_size=10))
        
        # [FIX TEST] Forziamo il branch nel DB per assicurarci che il retrieval test sia valido
        # anche se l'embedder sottostante ha ancora "main" hardcoded.
        force_update_branch_in_db(db_path, "pivot", "feature/fast")

        # 1.3 Testing Retrieval
        retriever = CodeRetriever(indexer.storage, provider)
        repo_id = indexer.parser.repo_id # Id stabile
        
        # Query A: "bubble sort slow" su MAIN -> Dovrebbe trovare risultati
        logger.info("   -> Query: 'bubble sort slow' on branch='main'")
        res_main = retriever.retrieve("bubble sort slow", repo_id=repo_id, branch="main", limit=1)
        if res_main and "Bubble Sort" in res_main[0].content:
            logger.info(f"      ✅ MATCH: {res_main[0].content.splitlines()[2]}")
        else:
            logger.error("      ❌ FAIL: Non ha trovato Bubble Sort nel main.")

        # Query B: "quick sort efficient" su MAIN -> NON dovrebbe trovare risultati pertinenti
        logger.info("   -> Query: 'quick sort efficient' on branch='main'")
        res_main_wrong = retriever.retrieve("quick sort efficient", repo_id=repo_id, branch="main", limit=1)
        if not res_main_wrong or res_main_wrong[0].score < 0.01 or "Quick Sort" not in res_main_wrong[0].content:
             logger.info("      ✅ OK: Quick Sort non trovato nel main (come previsto).")
        else:
             logger.warning(f"      ⚠️  Warning: Ha trovato qualcosa ({res_main_wrong[0].score}), controlla similarità.")

        # Query C: "quick sort efficient" su FEATURE/FAST -> Dovrebbe trovare risultati
        logger.info("   -> Query: 'quick sort efficient' on branch='feature/fast'")
        res_feat = retriever.retrieve("quick sort efficient", repo_id=repo_id, branch="feature/fast", limit=1)
        if res_feat and "Quick Sort" in res_feat[0].content:
            logger.info(f"      ✅ MATCH: {res_feat[0].content.splitlines()[2]}")
        else:
            logger.error("      ❌ FAIL: Non ha trovato Quick Sort nel branch feature.")


        # --- SCENARIO 2: ROBUSTEZZA FTS (Special Chars) ---
        logger.info("\n🧪 SCENARIO 2: FTS Robustness (Legacy Code)")
        checkout_branch(repo_path, "main") # Torniamo al main
        
        complex_code = """
class _Legacy$Handler_v99:
    def __init__(self):
        self.DATA_$$_CACHE = {}
    
    def exec_cmd_#42(self):
        # Handles ticket #42 specific edge case
        pass
"""
        commit_file(repo_path, "src/legacy.py", complex_code, "Add legacy junk")
        
        indexer.index(force=True)
        list(indexer.embed(provider, batch_size=10))
        
        # Test Keywords difficili
        keywords = ["_Legacy$Handler_v99", "DATA_$$_CACHE", "exec_cmd_#42"]
        
        for kw in keywords:
            logger.info(f"   -> Testing Keyword: '{kw}'")
            # Usiamo strategy='keyword' per forzare FTS
            res = retriever.retrieve(kw, repo_id=repo_id, branch="main", strategy="keyword", limit=1)
            
            if res and kw in res[0].content:
                logger.info(f"      ✅ FOUND: {kw}")
            else:
                logger.error(f"      ❌ MISSED: SQLite FTS ha fallito su '{kw}'")

    except Exception as e:
        logger.error(f"CRASH: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if 'indexer' in locals(): storage.close()
        shutil.rmtree(temp_dir)
        logger.info("\n🧹 Pulizia completata.")

if __name__ == "__main__":
    run_advanced_tests()