import os
os.environ["TOKENIZERS_PARALLELISM"] = "false"
import sys
import shutil
import logging
import argparse
import subprocess
from typing import List, Dict

# --- SETUP PATH ---
# Aggiunge la cartella src al path per importare la libreria
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

try:
    from code_graph_indexer import CodebaseIndexer, CodeRetriever
    from code_graph_indexer.storage.sqlite import SqliteGraphStorage
    from code_graph_indexer.providers.embedding import FastEmbedProvider, DummyEmbeddingProvider
except ImportError as e:
    print(f"❌ Errore importazione: {e}")
    sys.exit(1)

# Configurazione Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger("DEMO")

def setup_persistent_repo(target_path: str):
    """Crea una repo dummy persistente se non esiste."""
    if os.path.exists(os.path.join(target_path, ".git")):
        logger.info(f"📂 Repository già esistente in: {target_path}")
        return

    logger.info(f"🛠️  Creazione repository dummy in: {target_path}")
    os.makedirs(target_path, exist_ok=True)
    
    # 1. Creazione file sorgenti di esempio
    src_dir = os.path.join(target_path, "src")
    os.makedirs(src_dir, exist_ok=True)
    
    with open(os.path.join(src_dir, "auth.py"), "w") as f:
        f.write("""
class AuthenticationService:
    def login(self, username, password):
        # Authenticates the user against the database
        print(f"User {username} logged in")
        return True
        
    def logout(self, username):
        print(f"User {username} logged out")
""")

    with open(os.path.join(src_dir, "database.py"), "w") as f:
        f.write("""
class DatabaseConnection:
    def __init__(self, uri):
        self.uri = uri
        
    def connect(self):
        print(f"Connected to {self.uri}")
        
    def execute_query(self, sql):
        # Executes SQL query safely
        return []
""")

    # 2. Inizializzazione Git
    # Necessario perché il MetadataProvider estrae branch e commit da git
    subprocess.run(["git", "init"], cwd=target_path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "demo@test.com"], cwd=target_path)
    subprocess.run(["git", "config", "user.name", "DemoUser"], cwd=target_path)
    subprocess.run(["git", "add", "."], cwd=target_path, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=target_path, stdout=subprocess.DEVNULL)

def main():
    parser = argparse.ArgumentParser(description="Demo Indexing & Retrieval")
    parser.add_argument("--reset", action="store_true", help="Cancella il DB e la Repo prima di partire")
    parser.add_argument("--dummy", action="store_true", help="Usa embedding casuali (se non hai fastembed installato)")
    args = parser.parse_args()

    # Percorsi Persistenti
    base_dir = os.path.abspath("demo_env")
    repo_path = os.path.join(base_dir, "my_project")
    db_path = os.path.join(base_dir, "knowledge_graph.db")

    if args.reset:
        if os.path.exists(base_dir):
            shutil.rmtree(base_dir)
            logger.warning("🗑️  Ambiente resettato.")

    os.makedirs(base_dir, exist_ok=True)

    # 1. SETUP AMBIENTE
    setup_persistent_repo(repo_path)
    storage = SqliteGraphStorage(db_path)
    
    # Scelta Provider Embedding
    if args.dummy:
        embedder = DummyEmbeddingProvider(dim=384)
        logger.info("🤖 Using Dummy Embedder (Random Vectors)")
    else:
        try:
            embedder = FastEmbedProvider()
            logger.info("🤖 Using FastEmbed (Jina v2)")
        except ImportError:
            logger.error("❌ 'fastembed' non trovato. Installa con `pip install fastembed` o usa --dummy")
            return

    # 2. INDEXING & EMBEDDING
    logger.info("\n--- FASE 1: INDEXING & EMBEDDING ---")
    
    indexer = CodebaseIndexer(repo_path, storage)
    
    # Step A: Parsing & Graph Building
    indexer.index() 
    
    # Step B: Vector Embedding
    # embed() è un generatore, usiamo list() per eseguirlo tutto
    logger.info("Generating Embeddings...")
    list(indexer.embed(embedder, batch_size=32))

    # 3. PREPARAZIONE RETRIEVAL
    # Recuperiamo l'ID interno (UUID) generato dal database per questa repo+branch
    repo_info = indexer.parser.metadata_provider.get_repo_info()
    repo_record = storage.get_repository_by_context(repo_info['url'], repo_info['branch'])
    
    if not repo_record:
        logger.error("❌ Errore critico: Repository non trovata nel DB dopo l'indexing.")
        return
        
    internal_repo_id = repo_record['id']
    logger.info(f"✅ Repo pronta per la ricerca. ID Interno: {internal_repo_id}")

    # 4. LOOP DI RICERCA
    retriever = CodeRetriever(storage, embedder)
    
    logger.info("\n--- FASE 2: INTERACTIVE RETRIEVAL ---")
    print(f"🔎 Cerca nel codice (es. 'login logic', 'database connection'). Scrivi 'exit' per uscire.")

    while True:
        try:
            query = input("\n💬 Query: ").strip()
            if not query: continue
            if query.lower() in ['exit', 'quit']: break

            # Eseguiamo la ricerca usando l'ID interno
            results = retriever.retrieve(query, repo_id=internal_repo_id, limit=3)
            
            if not results:
                print("⚠️  Nessun risultato trovato.")
                continue
                
            print(f"\n🏆 Top {len(results)} Risultati per '{query}':")
            for i, res in enumerate(results):
                print(f"\n[{i+1}] File: {res.file_path} (L{res.start_line}-{res.end_line})")
                print(f"    Score:   {res.score:.4f} ({res.retrieval_method})")
                print(f"    Type:    {res.chunk_type}")
                print(f"    Context: {res.parent_context or 'Root'}")
                print(f"    Code:    {res.content.splitlines()[0].strip()}...")
                
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Errore durante la ricerca: {e}")

    logger.info("\n👋 Bye!")
    storage.close()

if __name__ == "__main__":
    main()