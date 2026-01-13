import argparse
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

from crader import CodebaseIndexer  # noqa: E402

# Importiamo direttamente il reader (assicurati di aver creato il file src/crader/reader.py)
from crader.reader import CodeReader  # noqa: E402
from crader.storage.sqlite import SqliteGraphStorage  # noqa: E402

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
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
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
    if os.path.exists(db_path):
        os.remove(db_path)

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

    # List della root (ritorna List[Dict])
    try:
        items = reader.list_directory(repo_id, "")
        for item in items:
            icon = "📁" if item['type'] == 'dir' else "📄"
            print(f"{icon} {item['name']} ({item['path']})")

        # Trova un file
        first_file_obj = next((i for i in items if i['type'] == 'file'), None)

        if first_file_obj:
            fname = first_file_obj['path'] # Usa il path relativo
            logger.info(f"3️⃣  Test CodeReader: Lettura file '{fname}'...")

            file_data = reader.read_file(repo_id, fname)
            print("--- DATA ---")
            print(f"Path: {file_data['file_path']}")
            print(f"Size: {file_data['size_bytes']} bytes")
            print(f"Content:\n{file_data['content'][:50]}...") # Preview

            if fname.endswith(".py"):
                 logger.info("4️⃣  Test CodeReader: Lettura Range...")
                 partial = reader.read_file(repo_id, fname, start_line=1, end_line=1)
                 print(f"Snippet L1-1: {partial['content'].strip()}")
    except Exception as e:
        logger.error(f"Errore Reader: {e}")

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
