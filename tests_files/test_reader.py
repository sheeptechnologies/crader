import os
import sys
import shutil
import logging
import argparse
import subprocess

# --- SETUP PATH ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from code_graph_indexer import CodebaseIndexer
from code_graph_indexer.storage.sqlite import SqliteGraphStorage
# Importiamo direttamente il reader (assicurati di aver creato il file src/code_graph_indexer/reader.py)
from code_graph_indexer.reader import CodeReader 

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger("TEST_READER")

def create_dummy_files(repo_path):
    """Crea una struttura di file mista per il test."""
    os.makedirs(os.path.join(repo_path, "src"), exist_ok=True)
    os.makedirs(os.path.join(repo_path, "docs"), exist_ok=True)
    
    # 1. File Codice (Normalmente indicizzato)
    with open(os.path.join(repo_path, "src", "main.py"), "w") as f:
        f.write("def main():\n    print('Hello World')\n")

    # 2. File Config (Spesso ignorato)
    with open(os.path.join(repo_path, "config.json"), "w") as f:
        f.write('{"debug": true, "version": "1.0.0"}')

    # 3. File Minificato/Build (Sicuramente ignorato dall'indexer)
    with open(os.path.join(repo_path, "bundle.min.js"), "w") as f:
        f.write("function a(){return!0}var b=1;console.log(b);")

def run_reader_test(target_path=None):
    temp_dir = None
    if not target_path:
        temp_dir = "temp_reader_test_repo"
        if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
        os.makedirs(temp_dir)
        target_path = temp_dir
        logger.info(f"🛠️  Creazione repo dummy in: {target_path}")
        
        # Init git
        subprocess.run(["git", "init"], cwd=target_path, stdout=subprocess.DEVNULL)
        create_dummy_files(target_path)
        subprocess.run(["git", "add", "."], cwd=target_path, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-m", "init"], cwd=target_path, stdout=subprocess.DEVNULL)
    else:
        target_path = os.path.abspath(target_path)

    db_path = "reader_test.db"
    if os.path.exists(db_path): os.remove(db_path)
    
    storage = SqliteGraphStorage(db_path)
    indexer = CodebaseIndexer(target_path, storage)
    
    logger.info("1️⃣  Registrazione Repo nel DB...")
    # Eseguiamo index() per registrare il repo_id e il local_path nel DB
    indexer.index()
    
    # Recuperiamo l'ID univoco generato
    repo_id = indexer.parser.repo_id
    logger.info(f"✅ Repo ID: {repo_id}")

    # --- TEST READER ---
    logger.info("\n2️⃣  Test CodeReader: Listing...")
    reader = CodeReader(storage)
    
    # List della root
    listing_output = reader.list_directory(repo_id, "docs")
    print(f"\n{listing_output}\n")

    # Parsing dell'output per trovare il primo file
    # L'output è del tipo:
    # 📁 folder
    # 📄 file.txt
    first_file = None
    for line in listing_output.splitlines():
        if "📄" in line:
            first_file = line.replace("📄 ", "").strip()
            break
            
    if first_file:
        logger.info(f"3️⃣  Test CodeReader: Lettura file '{first_file}'...")
        content = reader.read_file(repo_id, first_file)
        
        print("-" * 40)
        print(content)
        print("-" * 40)
        
        if "Error" not in content:
            logger.info("✅ Lettura riuscita!")
        else:
            logger.error("❌ Errore nella lettura.")
            
        # Test lettura range (se il file ha abbastanza righe, es. main.py)
        if first_file.endswith(".py"):
             logger.info(f"4️⃣  Test CodeReader: Lettura Range (L1-1)...")
             partial = reader.read_file(repo_id, first_file, start_line=1, end_line=1)
             print(f"Snippet:\n{partial}")

    else:
        logger.warning("⚠️  Nessun file trovato nella root.")

    # Cleanup
    storage.close()
    if temp_dir and os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    if os.path.exists(db_path):
        os.remove(db_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", help="Path della repo da testare")
    args = parser.parse_args()
    run_reader_test(args.path)