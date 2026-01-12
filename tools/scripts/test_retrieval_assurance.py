import logging
import os
import shutil
import sqlite3
import sys

# Setup Path
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path: sys.path.insert(0, src_dir)

from crader import CodebaseIndexer, CodeRetriever

# Usiamo DummyProvider per essere deterministici e veloci nel test,
# ma la logica di flow è identica a FastEmbed.
from crader.providers.embedding import DummyEmbeddingProvider
from crader.storage.sqlite import SqliteGraphStorage

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("ASSURANCE_TEST")

def setup_targeted_repo(path):
    """
    Crea una repo con contenuti specifici per testare Keyword vs Vector.
    """
    if os.path.exists(path): shutil.rmtree(path)
    os.makedirs(path)

    os.makedirs(os.path.join(path, "src"), exist_ok=True)

    # File 1: Contiene keyword uniche e "strane" (Facile per FTS, difficile per Vector)
    with open(os.path.join(path, "src", "legacy_api.py"), "w") as f:
        f.write("""
def XYZ_INTERNAL_HANDLE_v99():
    # Questa funzione ha un nome molto specifico
    # Serve per testare la keyword search esatta
    return "Legacy_Result"
""")

    # File 2: Contiene concetti semantici (Facile per Vector, ma senza keyword uniche nel prompt)
    with open(os.path.join(path, "src", "auth.py"), "w") as f:
        f.write("""
class SecurityManager:
    def verify_credentials(self, user, token):
        # Logic to check user identity against database
        # Encryption handling here
        pass
""")

    # Init Git
    import subprocess
    subprocess.run(["git", "init"], cwd=path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "add", "."], cwd=path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, stdout=subprocess.DEVNULL)

def verify_db_integrity(db_path, repo_id):
    """Controlla direttamente nel DB che i dati siano coerenti."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Conta nodi totali
    # Nota: la join serve per filtrare sul repo_id
    cursor.execute("""
        SELECT COUNT(*) FROM nodes n
        JOIN files f ON n.file_id = f.id
        WHERE f.repo_id = ? AND n.type NOT IN ('external_library', 'program', 'module')
    """, (repo_id,))
    total_nodes = cursor.fetchone()[0]

    # Conta embedding totali
    cursor.execute("SELECT COUNT(*) FROM node_embeddings WHERE repo_id = ?", (repo_id,))
    total_embeddings = cursor.fetchone()[0]

    conn.close()

    logger.info(f"📊 DB Stat Check: Nodi={total_nodes}, Embeddings={total_embeddings}")

    if total_embeddings == 0:
        return "empty"
    elif total_embeddings == total_nodes:
        return "full"
    else:
        return "partial"

def run_assurance_test():
    repo_path = os.path.abspath("test_assurance_repo")
    db_path = "assurance_test.db"

    if os.path.exists(db_path): os.remove(db_path)

    logger.info("🛠️  Setup Repo di Test...")
    setup_targeted_repo(repo_path)

    storage = SqliteGraphStorage(db_path)
    indexer = CodebaseIndexer(repo_path, storage)
    embedder = DummyEmbeddingProvider(dim=384) # Dimensione standard
    retriever = CodeRetriever(storage, embedder)

    try:
        # --- FASE 1: INDEXING (NO EMBEDDING) ---
        logger.info("\n1️⃣  Esecuzione Indexing (Solo Parsing)...")
        indexer.index()
        repo_id = indexer.parser.repo_id

        # VERIFICA 1: La ricerca Keyword DEVE funzionare SENZA embedding
        # Questo conferma che il bug della JOIN è risolto.
        logger.info("🧪 TEST A: Keyword Search senza Embedding...")
        res_kw = retriever.retrieve("XYZ_INTERNAL_HANDLE_v99", repo_id, strategy="keyword")

        if len(res_kw) > 0 and "legacy_api.py" in res_kw[0].file_path:
            logger.info("✅ PASS: Keyword Search funziona sui dati grezzi.")
        else:
            raise AssertionError("❌ FAIL: Keyword Search fallita o vuota! Il bug della JOIN persiste?")

        # VERIFICA 2: La ricerca Vector DEVE essere vuota (o gestita) ma non crashare
        logger.info("🧪 TEST B: Vector Search senza Embedding...")
        res_vec = retriever.retrieve("security login", repo_id, strategy="vector")
        if len(res_vec) == 0:
            logger.info("✅ PASS: Vector Search vuota come atteso.")
        else:
             logger.warning(f"⚠️ Warning: Vector Search ha ritornato risultati inaspettati: {len(res_vec)}")

        # --- FASE 2: GENERAZIONE EMBEDDINGS ---
        logger.info("\n2️⃣  Generazione Embeddings...")
        list(indexer.embed(embedder)) # Consuma il generatore

        # VERIFICA 3: Integrità del Database
        logger.info("🧪 TEST C: Integrità DB (Nodes vs Embeddings)...")
        status = verify_db_integrity(db_path, repo_id)
        if status == "full":
            logger.info("✅ PASS: Tutti i nodi eleggibili hanno un embedding.")
        else:
            raise AssertionError(f"❌ FAIL: Copertura embedding incoerente ({status}).")

        # --- FASE 3: RETRIEVAL COMPLETO ---
        logger.info("\n3️⃣  Test Retrieval Completo...")

        # VERIFICA 4: Vector Search ORA deve funzionare
        # Cerchiamo un concetto ("login credentials") che non c'è come keyword esatta
        logger.info("🧪 TEST D: Vector Search semantica...")
        res_vec_final = retriever.retrieve("login credentials encryption", repo_id, strategy="vector")

        # DummyEmbeddingProvider ritorna vettori random, quindi la "semantica" è casuale,
        # ma DEVE ritornare dei risultati tecnici se il DB è popolato.
        if len(res_vec_final) > 0:
            logger.info(f"✅ PASS: Vector Search ha trovato {len(res_vec_final)} risultati.")
        else:
            raise AssertionError("❌ FAIL: Vector Search ancora vuota dopo l'embedding!")

        # VERIFICA 5: Hybrid Search
        # Deve trovare SIA la keyword esatta SIA i vettori
        logger.info("🧪 TEST E: Hybrid Search...")
        # Usiamo una query che contiene la keyword "XYZ..." per forzare il match testuale
        res_hyb = retriever.retrieve("XYZ_INTERNAL_HANDLE_v99 security", repo_id, strategy="hybrid")

        found_legacy = any("legacy_api.py" in r.file_path for r in res_hyb)
        if found_legacy and len(res_hyb) > 0:
             logger.info("✅ PASS: Hybrid Search ha trovato il file tramite keyword.")
        else:
             raise AssertionError("❌ FAIL: Hybrid Search non ha trovato la keyword critica.")

        logger.info("\n🎉 TUTTI I TEST DI SICUREZZA SUPERATI!")

    except Exception as e:
        logger.error(f"\n📛 CRITICAL FAILURE: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if 'storage' in locals(): storage.close()
        if os.path.exists(repo_path): shutil.rmtree(repo_path)
        if os.path.exists(db_path): os.remove(db_path)

if __name__ == "__main__":
    run_assurance_test()
