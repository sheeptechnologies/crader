import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

try:
    from crader.indexer import CodebaseIndexer
    from crader.providers.embedding import DummyEmbeddingProvider
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from crader.indexer import CodebaseIndexer
    from crader.providers.embedding import DummyEmbeddingProvider

def create_dummy_repo(base_path: str) -> str:
    repo_path = os.path.join(base_path, "dummy-finance-repo")
    if os.path.exists(repo_path):
        shutil.rmtree(repo_path)
    os.makedirs(repo_path)

    src_path = os.path.join(repo_path, "src")
    os.makedirs(src_path)

    # 1. Creazione file sorgenti
    with open(os.path.join(src_path, "database.py"), "w") as f:
        f.write("""
class DatabaseConnection:
    def __init__(self, uri: str):
        self.uri = uri
        self.is_connected = False

    def connect(self):
        print(f"Connecting to {self.uri}")
        self.is_connected = True

    def query(self, sql: str):
        if not self.is_connected:
            raise Exception("Not connected")
        return [{"id": 1, "value": 100}]
""")

    with open(os.path.join(src_path, "processor.py"), "w") as f:
        f.write("""
from .database import DatabaseConnection

class PaymentProcessor:
    def __init__(self, db: DatabaseConnection):
        self.db = db

    def process_payment(self, amount: int):
        users = self.db.query("SELECT * FROM users")        print(f"Processing {amount} for users: {users}")
        return True
""")

    with open(os.path.join(repo_path, "main.py"), "w") as f:
        f.write("""
from src.database import DatabaseConnection
from src.processor import PaymentProcessor

def main():
    db = DatabaseConnection("postgres://localhost:5432")
    db.connect()
    processor = PaymentProcessor(db)
    processor.process_payment(500)

if __name__ == "__main__":
    main()
""")

    # 4. Inizializzazione Git
    try:
        subprocess.run(["git", "init"], cwd=repo_path, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=False)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_path, check=False)

        # [FIX] Aggiungiamo un remote esplicito per evitare di ereditare config strani
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/test-org/dummy-finance.git"],
            cwd=repo_path,
            check=False
        )

        subprocess.run(["git", "add", "."], cwd=repo_path, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True, stdout=subprocess.DEVNULL)
    except Exception as e:
        logger.warning(f"Git init fallito: {e}")

    return repo_path

def run_test(target_repo_path=None):
    temp_dir = tempfile.mkdtemp()

    try:
        if target_repo_path:
            repo_path = target_repo_path
            print(f"📂 Target: {repo_path}")
        else:
            print("🛠️  Creazione repository di test...")
            repo_path = create_dummy_repo(temp_dir)
            print(f"📂 Repository creata: {repo_path}")

        # --- INDEXING ---
        print("\n1️⃣  Avvio INDEXING...")
        indexer = CodebaseIndexer(repo_path)
        indexer.index()

        # --- EMBEDDING ---
        print("\n2️⃣  Avvio EMBEDDING...")
        provider = DummyEmbeddingProvider(dim=1536)

        generated_docs = []
        for item in indexer.embed(provider, batch_size=5, debug=True):
            if "status" not in item:
                generated_docs.append(item)

        # --- VERIFICA URL E ID ---
        if generated_docs:
            doc = generated_docs[0]
            print(f"\n✅ Verifica Repo ID: {doc['repo_id']}")

            # Verifica che l'URL nel DB sia pulito (accedendo allo storage)
            repo_info = indexer.storage.get_repository(doc['repo_id'])
            if repo_info:
                print(f"✅ URL Salvato a DB: {repo_info['url']}")
                if "filippo" in repo_info['url'] or "%20" in repo_info['url']:
                    print("❌ ERRORE: L'URL contiene ancora dati sensibili!")
                else:
                    print("✅ URL Sanitizzato correttamente.")

        # --- EXPORT ---
        if generated_docs:
            output_file = "debug_embeddings_context.json"
            json_output = []
            for doc in generated_docs:
                doc_copy = doc.copy()
                if 'vector' in doc_copy:
                    doc_copy['vector'] = doc_copy['vector'][:5] + ["..."]
                json_output.append(doc_copy)

            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(json_output, f, indent=2)
            print(f"\n💾 DUMP SALVATO: {output_file}")

    except Exception as e:
        print(f"\n❌ CRASH: {e}")
        import traceback
        traceback.print_exc()
    finally:
        indexer.close()
        if not target_repo_path:
            shutil.rmtree(temp_dir)

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    run_test(target)
