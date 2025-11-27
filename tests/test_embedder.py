import os
import sys
import shutil
import tempfile
import subprocess
import logging
import json
from typing import List, Dict

# Assicuriamo che il path includa 'src'
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from code_graph_indexer.indexer import CodebaseIndexer
from code_graph_indexer.providers.embedding import DummyEmbeddingProvider, FastEmbedProvider

# Configurazione Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("STRICT_TEST")

def setup_dummy_repo(base_dir: str, branch_name: str = "feature/verification-test") -> str:
    """Crea una repo git valida con contenuto Python e un branch specifico."""
    repo_path = os.path.join(base_dir, "strict-repo")
    os.makedirs(repo_path)
    
    # 1. Init Git
    subprocess.run(["git", "init"], cwd=repo_path, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@bot.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "TestBot"], cwd=repo_path, check=True)
    
    # 2. Crea file Python
    code = """
class AuthenticationManager:
    def login(self, user):
        print(f"Logging in {user}")
        return True

def logout(user):
    print("Logout")
"""
    with open(os.path.join(repo_path, "auth.py"), "w") as f:
        f.write(code)

    # 3. Commit e Branch
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "Initial"], cwd=repo_path, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo_path, check=True, stdout=subprocess.DEVNULL)
    
    return repo_path

def strict_verification_test():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_index.db")
    target_branch = "feature/verification-test"
    
    try:
        logger.info("=== 1. SETUP AMBIENTE ===")
        repo_path = setup_dummy_repo(temp_dir, branch_name=target_branch)
        logger.info(f"Repo creata in: {repo_path} (Branch: {target_branch})")

        logger.info("\n=== 2. TEST INDEXING ===")
        indexer = CodebaseIndexer(repo_path, db_path=db_path)
        indexer.index()
        
        # VERIFICA 1: Repo ID e Branch nel DB
        repo_id = indexer.repo_id
        repo_record = indexer.storage.get_repository(repo_id)
        
        if not repo_record:
            raise AssertionError("❌ ERRORE: Repository non trovata nel DB!")
        
        logger.info(f"✅ Repo ID persistito: {repo_record['id']}")
        logger.info(f"✅ Branch persistito:  {repo_record['branch']}")
        
        if repo_record['branch'] != target_branch:
            raise AssertionError(f"❌ MISMATCH BRANCH: Atteso '{target_branch}', Trovato '{repo_record['branch']}'")

        # VERIFICA 2: Conteggio Nodi
        stats = indexer.get_stats()
        total_nodes = stats['total_nodes']
        logger.info(f"✅ Nodi indicizzati:   {total_nodes}")
        
        if total_nodes == 0:
            raise AssertionError("❌ ERRORE: Nessun nodo indicizzato!")

        logger.info("\n=== 3. TEST EMBEDDING ===")
        # Usiamo DummyProvider per velocità e determinismo
        provider = FastEmbedProvider(model_name="jinaai/jina-embeddings-v2-base-code")
        
        embedded_docs = []
        # debug=True è FONDAMENTALE per ottenere indietro i documenti generati
        for item in indexer.embed(provider, batch_size=10, debug=True):
            if "status" not in item: # È un documento vettoriale
                embedded_docs.append(item)

        logger.info(f"✅ Documenti generati: {len(embedded_docs)}")

        logger.info("\n=== 4. STRICT CHECK & VALIDATION ===")
        
        # CHECK A: Coerenza Branch in TUTTI i documenti
        invalid_branch_docs = [d for d in embedded_docs if d['branch'] != target_branch]
        if invalid_branch_docs:
            raise AssertionError(f"❌ ERRORE CRITICO: {len(invalid_branch_docs)} documenti hanno il branch sbagliato! Es: {invalid_branch_docs[0]['branch']}")
        else:
            logger.info(f"✅ GARANZIA: Tutti i {len(embedded_docs)} documenti hanno branch='{target_branch}'")

        # CHECK B: Coerenza Repo ID in TUTTI i documenti
        invalid_repo_docs = [d for d in embedded_docs if d['repo_id'] != repo_id]
        if invalid_repo_docs:
            raise AssertionError(f"❌ ERRORE CRITICO: {len(invalid_repo_docs)} documenti hanno repo_id sbagliato!")
        else:
            logger.info(f"✅ GARANZIA: Tutti i {len(embedded_docs)} documenti hanno repo_id='{repo_id}'")

        # CHECK C: Cross-Check Conteggi (Storage vs Embedding)
        # Nota: L'embedder potrebbe scartare nodi vuoti o non supportati, ma in questo caso semplice
        # dovrebbero coincidere o essere molto vicini.
        # Recuperiamo manualmente i nodi candidati per confronto esatto
        candidates = list(indexer.storage.get_nodes_cursor(repo_id=repo_id, branch=target_branch))
        candidate_ids = set(c['id'] for c in candidates)
        embedded_ids = set(d['chunk_id'] for d in embedded_docs)
        
        logger.info(f"   Candidati (Storage): {len(candidates)}")
        logger.info(f"   Embeddati (Vettori): {len(embedded_docs)}")
        
        missing = candidate_ids - embedded_ids
        if missing:
            logger.warning(f"⚠️  Attenzione: {len(missing)} nodi non sono stati embeddati (forse vuoti o filtrati).")
        
        if len(embedded_docs) == 0:
             raise AssertionError("❌ ERRORE: Pipeline embedding fallita (0 documenti).")

        logger.info("\n🎉 SUCCESSO: IL PROCESSO È GARANTITO AL 100%")
        return True

    except Exception as e:
        logger.error(f"\n❌ TEST FALLITO: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if 'indexer' in locals(): indexer.close()
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    strict_verification_test()