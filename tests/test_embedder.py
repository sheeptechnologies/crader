import os
import sys
import json
import shutil
import tempfile
import subprocess
import struct
import logging
from typing import Dict, Any

# Configurazione Logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Import dinamico
try:
    from code_graph_indexer.indexer import CodebaseIndexer
    from code_graph_indexer.providers.embedding import DummyEmbeddingProvider
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from code_graph_indexer.indexer import CodebaseIndexer
    from code_graph_indexer.providers.embedding import DummyEmbeddingProvider

def create_dummy_repo(base_path: str) -> str:
    """
    Crea una repository Python realistica per testare le relazioni semantiche.
    """
    repo_path = os.path.join(base_path, "dummy-finance-repo")
    if os.path.exists(repo_path):
        shutil.rmtree(repo_path)
    os.makedirs(repo_path)
    
    src_path = os.path.join(repo_path, "src")
    os.makedirs(src_path)

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
        users = self.db.query("SELECT * FROM users") 
        print(f"Processing {amount} for users: {users}")
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

    try:
        subprocess.run(["git", "init"], cwd=repo_path, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=False)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_path, check=False)
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

        # --- 2. INDEXING ---
        print("\n1️⃣  Avvio INDEXING...")
        indexer = CodebaseIndexer(repo_path)
        indexer.index()
        
        stats = indexer.get_stats()
        print(f"   ✅ Indexing completato.")
        print(f"      - File: {stats.get('files', 0)}")
        print(f"      - Nodi totali: {stats.get('total_nodes', 0)}")

        # --- 3. EMBEDDING ---
        print("\n2️⃣  Avvio EMBEDDING (Stream & Batching)...")
        provider = DummyEmbeddingProvider(dim=1536)
        
        generated_docs = []
        
        # IMPORTANTE: debug=True per ricevere i documenti indietro
        for item in indexer.embed(provider, batch_size=5, debug=True):
            if "status" in item:
                print(f"   [Progress] {item['processed']} nodi elaborati...")
            else:
                generated_docs.append(item)

        print(f"   ✅ Embedding completato. Generati {len(generated_docs)} documenti.")

        # --- 4. EXPORT DEBUG ---
        if generated_docs:
            output_file = "debug_embeddings_context.json"
            
            json_output = []
            for doc in generated_docs:
                doc_copy = doc.copy()
                if 'vector' in doc_copy:
                    doc_copy['vector'] = doc_copy['vector'][:5] + ["... (truncated)"]
                json_output.append(doc_copy)

            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(json_output, f, indent=2)
            
            print(f"\n💾 DUMP SALVATO: {output_file}")
            
            print("\n🔎 INSPECTION: ESEMPIO DI PAYLOAD ARRICCHITO")
            example = next((d for d in json_output if "query" in d.get('text_content', '')), json_output[0])
            
            print("=" * 60)
            print(f"NODE ID:   {example['chunk_id']}")
            print(f"TYPE:      {example['chunk_type']}")
            print(f"FILE:      {example['file_path']}")
            print("-" * 60)
            print("CONTENUTO DEL CAMPO '_debug_context' (Input per l'Embedding):")
            print("-" * 60)
            print(example.get('_debug_context', 'N/A'))
            print("=" * 60)
            
            if "[DEFINITIONS]" in example.get('_debug_context', ''):
                print("✅ SUCCESSO: Il payload contiene le definizioni SCIP!")
            else:
                print("⚠️  NOTA: Se non vedi [DEFINITIONS], verifica che 'scip' sia installato.")

        else:
            print("❌ ERRORE: Nessun documento generato.")

    except Exception as e:
        print(f"\n❌ CRASH: {e}")
        import traceback
        traceback.print_exc()
    finally:
        indexer.close()
        if not target_repo_path:
            shutil.rmtree(temp_dir)
            print("\n🧹 Pulizia completata.")

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    run_test(target)