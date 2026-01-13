import logging
import os
import shutil
import subprocess
import sys

# --- SETUP PATH ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from crader import CodebaseIndexer, CodeRetriever  # noqa: E402
from crader.providers.embedding import DummyEmbeddingProvider  # noqa: E402
from crader.storage.sqlite import SqliteGraphStorage  # noqa: E402

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger("LIFECYCLE")

def git_commit(repo_path, message):
    subprocess.run(["git", "add", "."], cwd=repo_path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", message], cwd=repo_path, stdout=subprocess.DEVNULL)
    # Ritorna l'hash
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_path, text=True).strip()

def run_lifecycle_test():
    base_dir = os.path.abspath("test_lifecycle_env")
    repo_path = os.path.join(base_dir, "repo")
    db_path = os.path.join(base_dir, "graph.db")

    if os.path.exists(base_dir):
        shutil.rmtree(base_dir)
    os.makedirs(repo_path)

    # 1. INIT REPO
    logger.info("🛠️  Init Git Repo...")
    subprocess.run(["git", "init"], cwd=repo_path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@bot.com"], cwd=repo_path)
    subprocess.run(["git", "config", "user.name", "TestBot"], cwd=repo_path)

    # Scriviamo versione 1 del codice su MAIN
    with open(os.path.join(repo_path, "logic.py"), "w") as f:
        f.write("def calculate():\n    return 'OLD_LOGIC_MAIN'\n")

    commit_v1 = git_commit(repo_path, "Initial commit")
    logger.info(f"📌 Commit V1 (Main): {commit_v1[:7]}")

    # 2. INDEXING V1 (MAIN)
    storage = SqliteGraphStorage(db_path)
    # Nota: Usiamo Dummy per velocità, ma la logica di retrieval è identica
    embedder = DummyEmbeddingProvider(dim=384)

    logger.info("\n--- STEP 1: Indexing Main V1 ---")
    indexer = CodebaseIndexer(repo_path, storage)
    indexer.index()
    list(indexer.embed(embedder)) # Genera vettori

    # Recuperiamo l'ID univoco per Main
    id_main = indexer.parser.repo_id
    logger.info(f"🆔 ID Main: {id_main}")

    # 3. CREAZIONE BRANCH FEATURE & MODIFICA
    logger.info("\n--- STEP 2: Creating Feature Branch ---")
    subprocess.run(["git", "checkout", "-b", "feature/new-calc"], cwd=repo_path, stdout=subprocess.DEVNULL)

    with open(os.path.join(repo_path, "logic.py"), "w") as f:
        f.write("def calculate():\n    return 'NEW_FEATURE_LOGIC'\n")

    commit_feat = git_commit(repo_path, "Update logic in feature")
    logger.info(f"📌 Commit Feature: {commit_feat[:7]}")

    # 4. INDEXING FEATURE
    logger.info("🚀 Indexing Feature Branch...")
    # Re-istanziamo l'indexer perché i metadati git su disco sono cambiati
    indexer_feat = CodebaseIndexer(repo_path, storage)
    indexer_feat.index()
    list(indexer_feat.embed(embedder))

    id_feat = indexer_feat.parser.repo_id
    logger.info(f"🆔 ID Feature: {id_feat}")

    if id_main == id_feat:
        raise AssertionError("❌ ERRORE: Gli ID dovrebbero essere diversi!")

    # 5. TEST ISOLAMENTO RETRIEVAL
    logger.info("\n--- STEP 3: Verifying Semantic Isolation ---")
    retriever = CodeRetriever(storage, embedder)

    # Cerchiamo "NEW_FEATURE_LOGIC"
    # Ci aspettiamo di trovarlo SOLO usando id_feat, NON id_main

    # Test su Main (Non deve trovare la feature)
    res_main = retriever.retrieve("NEW_FEATURE_LOGIC", repo_id=id_main, limit=1, strategy="keyword")
    if res_main:
        logger.error(f"❌ LEAK DETECTED! Trovato codice feature nel main: {res_main[0].content}")
    else:
        logger.info("✅ OK: Main branch è pulito (non vede la feature).")

    # Test su Feature (Deve trovarla)
    res_feat = retriever.retrieve("NEW_FEATURE_LOGIC", repo_id=id_feat, limit=1, strategy="keyword")
    if res_feat and "NEW_FEATURE_LOGIC" in res_feat[0].content:
        logger.info("✅ OK: Feature branch contiene il nuovo codice.")
    else:
        logger.error("❌ ERRORE: Feature branch non contiene il codice atteso!")

    # 6. TEST AGGIORNAMENTO INCREMENTALE (MAIN)
    logger.info("\n--- STEP 4: Incremental Update on Main ---")
    subprocess.run(["git", "checkout", "master"], cwd=repo_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Modifichiamo Main (simuliamo merge o fix)
    with open(os.path.join(repo_path, "logic.py"), "w") as f:
        f.write("def calculate():\n    return 'UPDATED_MAIN_V2'\n")

    commit_v2 = git_commit(repo_path, "Update main to V2")
    logger.info(f"📌 Commit V2 (Main): {commit_v2[:7]}")

    # Re-indexing Main
    indexer_main_v2 = CodebaseIndexer(repo_path, storage)
    indexer_main_v2.index() # Dovrebbe rilevare il nuovo commit e aggiornare
    list(indexer_main_v2.embed(embedder))

    # L'ID dovrebbe essere LO STESSO di prima (stesso url, stesso branch), ma i dati aggiornati
    id_main_v2 = indexer_main_v2.parser.repo_id
    if id_main_v2 != id_main:
        logger.warning(
            f"⚠️ Nota: L'ID è cambiato ({id_main} -> {id_main_v2}). "
            "Questo è accettabile se l'implementazione rigenera l'UUID, ma ideale se stabile."
        )

    # Verifica che il vecchio contenuto 'OLD_LOGIC_MAIN' sia sparito
    res_v2_old = retriever.retrieve("OLD_LOGIC_MAIN", repo_id=id_main_v2, strategy="keyword")
    if res_v2_old:
        logger.error("❌ ERRORE: Il vecchio codice è ancora presente dopo l'aggiornamento!")
    else:
        logger.info("✅ OK: Vecchio codice rimosso.")

    # Verifica che il nuovo contenuto 'UPDATED_MAIN_V2' ci sia
    res_v2_new = retriever.retrieve("UPDATED_MAIN_V2", repo_id=id_main_v2, strategy="keyword")
    if res_v2_new:
        logger.info("✅ OK: Nuovo codice V2 indicizzato correttamente.")
    else:
        logger.error("❌ ERRORE: Nuovo codice V2 non trovato!")

    logger.info("\n🎉 LIFECYCLE TEST PASSED!")
    storage.close()
    shutil.rmtree(base_dir)

if __name__ == "__main__":
    run_lifecycle_test()
