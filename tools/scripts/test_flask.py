import atexit
import logging
import os
import shutil
import subprocess
import sys
import tempfile

# Configurazione Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TEST_STACK")

# --- PATH SETUP ---
# Assumiamo di essere nella root del progetto o nella cartella tests
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, "..", "src"))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Import componenti
try:
    from crader import CodebaseIndexer, CodeNavigator, CodeReader, CodeRetriever
    from crader.storage.postgres import PostgresGraphStorage
    # Fallback import per provider
    try:
        from crader.providers.openai_emb import OpenAIEmbeddingProvider
    except ImportError:
        from crader.providers.embedding import OpenAIEmbeddingProvider
except ImportError as e:
    logger.error(f"Import Error: {e}. Assicurati di aver installato il pacchetto o settato PYTHONPATH.")
    sys.exit(1)

# --- CONFIGURAZIONE ---
DB_PORT = "5433"
DB_URL = f"postgresql://sheep_user:sheep_password@localhost:{DB_PORT}/sheep_index"
REPO_URL = "https://github.com/pallets/flask.git"

# Directory temporanea per il clone
REPO_PATH = tempfile.mkdtemp(prefix="test_flask_")

def cleanup():
    if os.path.exists(REPO_PATH):
        shutil.rmtree(REPO_PATH)
        logger.info(f"🧹 Pulizia completata: {REPO_PATH}")

atexit.register(cleanup)

def main():
    logger.info("🚀 AVVIO TEST COMPLETO STACK (Reader & Navigator)")

    # 1. SETUP AMBIENTE
    logger.info(f"⬇️  Cloning Flask in {REPO_PATH}...")
    subprocess.run(
        ["git", "clone", "--depth", "1", REPO_URL, REPO_PATH],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    try:
        storage = PostgresGraphStorage(DB_URL)
        provider = OpenAIEmbeddingProvider(model="text-embedding-3-small")
    except Exception as e:
        logger.critical(f"❌ Errore connessione DB/OpenAI: {e}")
        return

    indexer = CodebaseIndexer(REPO_PATH, storage)
    repo_meta = indexer.parser.metadata_provider.get_repo_info()

    # 2. INDEXING & SNAPSHOT
    logger.info("🔨 Esecuzione Indexing...")
    try:
        snapshot_id = indexer.index(force=True) # Forziamo per essere sicuri di testare la scrittura
        logger.info(f"✅ Indexing OK. Snapshot ID: {snapshot_id}")
    except Exception as e:
        logger.critical(f"❌ Indexing fallito: {e}")
        return

    # Risolviamo anche repo_id per completezza (anche se non strettamente necessario per i nuovi metodi)
    repo_id = storage.ensure_repository(repo_meta['url'], repo_meta['branch'], repo_meta['name'])

    # 3. EMBEDDING
    logger.info("🤖 Esecuzione Embedding...")
    try:
        # Consumiamo il generatore
        for _ in indexer.embed(provider, batch_size=150, force_snapshot_id=snapshot_id):
            pass
        logger.info("✅ Embedding OK.")
    except Exception as e:
        logger.critical(f"❌ Embedding fallito: {e}")
        return

    # Inizializziamo i componenti di lettura
    retriever = CodeRetriever(storage, provider)
    reader = CodeReader(storage)
    navigator = CodeNavigator(storage)

    # ---------------------------------------------------------
    # TEST 4: RETRIEVER (Discovery)
    # ---------------------------------------------------------
    logger.info("\n--- TEST 4: RETRIEVER ---")
    query = "Flask application entry point"
    results = retriever.retrieve(query, repo_id=repo_id, snapshot_id=snapshot_id, limit=1)

    if not results:
        logger.error("❌ Retriever non ha trovato nulla! Impossibile proseguire i test sui nodi.")
        return

    target_node = results[0]
    logger.info(f"✅ Retriever OK. Trovato nodo: {target_node.node_id}")
    logger.info(f"   File: {target_node.file_path} (Score: {target_node.score:.4f})")

    # ---------------------------------------------------------
    # TEST 5: CODE READER (Virtual Filesystem)
    # ---------------------------------------------------------
    logger.info("\n--- TEST 5: CODE READER ---")

    # A. List Directory
    try:
        root_items = reader.list_directory(snapshot_id, "")
        logger.info(f"📂 ls / -> {[i['name'] for i in root_items[:5]]}...")

        # Verifica banale: Flask ha una cartella 'src'
        has_src = any(i['name'] == 'src' and i['type'] == 'dir' for i in root_items)
        if has_src:
            logger.info("   ✅ list_directory: OK (Trovata 'src')")
        else:
            logger.error("   ❌ list_directory: FAIL ('src' non trovata)")

        # Test subfolder se esiste src
        if has_src:
            src_items = reader.list_directory(snapshot_id, "src")
            logger.info(f"   ls src/ -> {[i['name'] for i in src_items]}")
    except Exception as e:
        logger.error(f"   ❌ list_directory EXCEPTION: {e}")

    # B. Find Directories
    try:
        found = reader.find_directories(snapshot_id, "json", limit=5)
        logger.info(f"🔍 find 'json' -> {found}")
        if found:
            logger.info("   ✅ find_directories: OK")
        else:
            logger.warning("   ⚠️ find_directories: Nessun risultato (Flask potrebbe aver cambiato struttura)")
    except Exception as e:
        logger.error(f"   ❌ find_directories EXCEPTION: {e}")

    # C. Read File
    try:
        # Leggiamo il file trovato dal retriever
        target_path = target_node.file_path
        logger.info(f"📖 Reading file: {target_path} (Lines 1-20)")

        file_data = reader.read_file(snapshot_id, target_path, start_line=1, end_line=20)
        content_preview = file_data['content'].replace('\n', '\\n')[:100]

        logger.info(f"   Content Preview: {content_preview}...")
        if len(file_data['content']) > 0:
            logger.info("   ✅ read_file: OK")
        else:
            logger.error("   ❌ read_file: FAIL (Contenuto vuoto)")
    except Exception as e:
        logger.error(f"   ❌ read_file EXCEPTION: {e}")

    # ---------------------------------------------------------
    # TEST 6: CODE NAVIGATOR (Graph Traversal)
    # ---------------------------------------------------------
    logger.info("\n--- TEST 6: CODE NAVIGATOR ---")
    node_id = target_node.node_id

    # A. Parent
    try:
        parent = navigator.read_parent_chunk(node_id)
        if parent:
            logger.info(f"⬆️  Parent: {parent.get('type')} (ID: {parent.get('id')})")
        else:
            logger.info("⬆️  Parent: None (È un nodo root)")
        logger.info("   ✅ read_parent_chunk: OK")
    except Exception as e:
        logger.error(f"   ❌ read_parent_chunk EXCEPTION: {e}")

    # B. Next Sibling
    try:
        nxt = navigator.read_neighbor_chunk(node_id, "next")
        if nxt:
            logger.info(f"➡️  Next: {nxt.get('type')} (ID: {nxt.get('id')})")
        else:
            logger.info("➡️  Next: None")
        logger.info("   ✅ read_neighbor_chunk: OK")
    except Exception as e:
        logger.error(f"   ❌ read_neighbor_chunk EXCEPTION: {e}")

    # C. Impact Analysis (Incoming)
    try:
        impact = navigator.analyze_impact(node_id)
        logger.info(f"⬅️  Impact (Incoming Refs): {len(impact)}")
        if impact:
            logger.info(f"    Esempio: {impact[0]['file']} -> {impact[0]['relation']}")
        logger.info("   ✅ analyze_impact: OK")
    except Exception as e:
        logger.error(f"   ❌ analyze_impact EXCEPTION: {e}")

    # D. Pipeline (Outgoing Tree)
    try:
        pipeline = navigator.visualize_pipeline(node_id, max_depth=1)
        calls = pipeline.get('call_graph', {})
        logger.info(f"⤵️  Pipeline (Outgoing Calls): {len(calls)} direct children")
        logger.info("   ✅ visualize_pipeline: OK")
    except Exception as e:
        logger.error(f"   ❌ visualize_pipeline EXCEPTION: {e}")

    logger.info("\n🏁 TEST COMPLETATO.")
    storage.close()

if __name__ == "__main__":
    main()
