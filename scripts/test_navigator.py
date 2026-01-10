import os
import sys
import shutil
import logging
import argparse
import time

# Setup Path per importare la libreria src
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path: sys.path.insert(0, src_dir)

from crader import CodebaseIndexer, CodeNavigator
from crader.storage.sqlite import SqliteGraphStorage
from crader.parsing.parser import TreeSitterRepoParser

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("NAV_TEST")

def setup_dummy_repo(path):
    """Crea la repo finta per il test standard."""
    if os.path.exists(path): shutil.rmtree(path)
    os.makedirs(path)
    
    code = """
class PaymentService:
    def __init__(self):
        self.connected = False

    def validate(self, amount):
        if amount < 0: return False
        return True

    def process(self, amount):
        if self.validate(amount):
            print("Processing...")
            return True
        return False
"""
    os.makedirs(os.path.join(path, "src"), exist_ok=True)
    with open(os.path.join(path, "src", "service.py"), "w") as f:
        f.write(code)
        
    import subprocess
    subprocess.run(["git", "init"], cwd=path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "add", "."], cwd=path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, stdout=subprocess.DEVNULL)

def run_test(target_path=None):
    db_path = "nav_test.db"
    
    # Configurazione Dinamica
    if target_path:
        # MODALITÀ CUSTOM REPO
        repo_path = os.path.abspath(target_path)
        is_dummy = False
        logger.info(f"🚀 Modalità Custom: Test su {repo_path}")
    else:
        # MODALITÀ DUMMY
        repo_path = os.path.abspath("test_nav_repo")
        setup_dummy_repo(repo_path)
        is_dummy = True
        logger.info("🚀 Modalità Dummy: Creata repo di test")

    # Patch per chunking fine-grained
    original_limit = TreeSitterRepoParser.MAX_CHUNK_SIZE
    if is_dummy:
        TreeSitterRepoParser.MAX_CHUNK_SIZE = 200 
        logger.info("🔧 MonkeyPatch: MAX_CHUNK_SIZE=200 (Granularità Metodo)")

    storage = SqliteGraphStorage(db_path)
    
    try:
        indexer = CodebaseIndexer(repo_path, storage)
        
        logger.info("1. Indexing...")
        indexer.index()
        
        # Recuperiamo tutti i nodi
        nodes = list(indexer.get_nodes())
        logger.info(f"📊 Totale Nodi indicizzati: {len(nodes)}")
        
        if not nodes:
            logger.error("❌ Nessun nodo trovato! Verifica il path.")
            return

        navigator = CodeNavigator(storage)

        # --- LOGICA DI TEST ---
        if is_dummy:
            _run_dummy_assertions(nodes, navigator)
        else:
            _run_generic_exploration(nodes, navigator)
        
    finally:
        # Cleanup
        TreeSitterRepoParser.MAX_CHUNK_SIZE = original_limit
        if 'storage' in locals(): storage.close()
        
        if is_dummy and os.path.exists(repo_path):
            shutil.rmtree(repo_path)
        
        if os.path.exists(db_path): os.remove(db_path) 

def _run_dummy_assertions(nodes, navigator):
    """Test rigido sulla struttura nota di PaymentService."""
    try:
        class_node = next(n for n in nodes if n['type'] == 'class')
        methods = [n for n in nodes if n['type'] in ('function', 'method') and 'service.py' in n['file_path']]
        methods.sort(key=lambda x: x['start_line'])
        
        if len(methods) < 3:
            logger.error(f"❌ Errore Chunking: Trovati solo {len(methods)} metodi su 3 attesi.")
            return

        init_node, val_node, proc_node = methods[0], methods[1], methods[2]
        logger.info(f"✅ Struttura riconosciuta: {class_node['id'][:8]} -> {val_node['id'][:8]}")

        # Test Parent
        logger.info("\n--- TEST 1: Parent ---")
        p_info = navigator.read_parent_chunk(val_node['id']) # Dict
        if p_info:
            print(f"Parent found: {p_info.get('type')} - {p_info.get('file_path')}")
            if p_info['id'] == class_node['id']: logger.info("✅ PASS")
            else: logger.error("❌ FAIL ID Mismatch")
        else:
            logger.error("❌ FAIL: No parent found")

        # Test Next
        logger.info("\n--- TEST 2: Next Chunk ---")
        nxt = navigator.read_neighbor_chunk(init_node['id'], "next") # Dict
        if nxt:
            content_preview = nxt.get('content', '').split('\n')[0]
            print(f"Next found: {nxt.get('type')} -> {content_preview}")
            if "def validate" in nxt.get('content', ''): logger.info("✅ PASS")
            else: logger.error("❌ FAIL Content Mismatch")
        else:
            logger.error("❌ FAIL: No next chunk")

        # Test Impact
        logger.info("\n--- TEST 3: Impact (Incoming Refs) ---")
        impact = navigator.analyze_impact(val_node['id']) # List[Dict]
        print(f"Impact list size: {len(impact)}")
        for i in impact:
            print(f" - {i['file']} L{i['line']} ({i['relation']})")
        
    except StopIteration:
        logger.error("❌ Impossibile trovare i nodi attesi nella dummy repo.")

def _run_generic_exploration(nodes, navigator):
    """Esplorazione su una repo arbitraria."""
    logger.info("\n🔍 ESPLORAZIONE GENERICA")
    
    candidates = [n for n in nodes if n['type'] in ('class', 'function', 'method')]
    
    if not candidates:
        logger.warning("⚠️ Nessuna classe/funzione trovata per navigare.")
        return

    target = candidates[len(candidates)//2]
    
    print(f"\n🎯 Nodo Target: {target['type']} in {target['file_path']} (L{target['start_line']})")
    print(f"   ID: {target['id']}")

    # 1. Parent
    print(f"\n[1] Parent Context:")
    parent = navigator.read_parent_chunk(target['id'])
    if parent:
        print(f"   Type: {parent.get('type')}")
        print(f"   File: {parent.get('file_path')}")
        print(f"   ID:   {parent.get('id')}")
    else:
        print("   (None)")

    # 2. Neighbors
    print(f"\n[2] Previous Chunk:")
    prev_chunk = navigator.read_neighbor_chunk(target['id'], "prev")
    if prev_chunk:
        content = prev_chunk.get('content', '')
        print(f"   Type: {prev_chunk.get('type')}")
        print(f"   Content Preview:\n{content[:200]}...")
    else:
        print("   (None)")
    
    print(f"\n[3] Next Chunk:")
    next_chunk = navigator.read_neighbor_chunk(target['id'], "next")
    if next_chunk:
        content = next_chunk.get('content', '')
        print(f"   Type: {next_chunk.get('type')}")
        print(f"   Content Preview:\n{content[:200]}...")
    else:
        print("   (None)")

    # 3. Impact
    print(f"\n[4] Impact Analysis (Chi usa questo nodo?):")
    impact = navigator.analyze_impact(target['id'])
    if impact:
        for i in impact: 
            print(f"   - {i.get('file')} L{i.get('line')} ({i.get('relation')})")
    else:
        print("   (Nessuna referenza trovata)")

    # 4. Pipeline
    print(f"\n[5] Pipeline (Cosa chiama questo nodo?):")
    pipeline = navigator.visualize_pipeline(target['id'])
    print(pipeline)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", help="Path della repo da analizzare (opzionale)")
    args = parser.parse_args()
    
    run_test(args.path)