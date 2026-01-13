import argparse
import json
import logging
import os
import sys
import time

# --- FIX IMPORT ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("TEST")

try:
    from crader import CodebaseIndexer
except ImportError as e:
    logger.error(f"❌ Errore importazione: {e}")
    sys.exit(1)

def test_indexing_full(repo_path: str, output_json: str = None, branch: str = "main"):
    repo_path = os.path.abspath(repo_path)
    if not os.path.isdir(repo_path):
        logger.error(f"Cartella non trovata: {repo_path}")
        return

    logger.info(f"🚀 TEST INDEXER SU: {repo_path} (Branch: {branch})")

    indexer = CodebaseIndexer(repo_path, branch=branch, db_url="postgresql://mock:mock@localhost:5432/mock")

    # MOCK STORAGE FOR DEMO
    from unittest.mock import MagicMock
    indexer.connector = MagicMock()
    indexer.storage = MagicMock()
    # Ensure ensure_repository returns a dummy ID
    indexer.storage.ensure_repository.return_value = "repo-123"
    # Ensure create_snapshot returns a dummy ID and new=True
    indexer.storage.create_snapshot.return_value = ("snap-123", True)
    # Ensure check_and_reset returns False
    indexer.storage.check_and_reset_reindex_flag.return_value = False

    logger.info("⚠️ RUNNING WITH MOCKED STORAGE (DB Bypass) ⚠️")

    # 1. ESECUZIONE
    logger.info("\n--- FASE 1: INDICIZZAZIONE ---")
    start_time = time.time()
    indexer.index()
    duration = time.time() - start_time
    logger.info(f"✅ Completata in {duration:.2f}s")

    # 2. RECUPERO DATI
    logger.info("\n--- RECUPERO DATI ---")

    files = list(indexer.get_files())       # <--- NUOVO
    nodes = list(indexer.get_nodes())
    edges = list(indexer.get_edges())
    contents = list(indexer.get_contents())

    stats = indexer.get_stats()

    logger.info(f"📂 File:       {len(files)}")
    logger.info(f"📦 Nodi:       {len(nodes)}")
    logger.info(f"📄 Contenuti:  {len(contents)}")
    logger.info(f"🔗 Archi:      {len(edges)}")

    # 3. DEBUG ANTEPRIMA
    logger.info("\n--- ANTEPRIMA FILE ---")
    if files:
        f = files[0]
        print(f"[FILE] {f['path']} (Lang: {f['language']}, Size: {f['size_bytes']}b)")
        print(f"       Repo: {f['repo_id']}")
        print(f"       Commit: {f['commit_hash']}")

    logger.info("\n--- ANTEPRIMA CHUNK ---")
    content_map = {c['chunk_hash']: c['content'] for c in contents}

    count = 0
    for node in nodes:
        if count >= 3: break
        raw_content = content_map.get(node.get('chunk_hash', ''), "[NO CONTENT]")
        preview = raw_content[:100].replace('\n', ' ↵ ')

        print(f"[{count+1}] {node['file_path']} (L{node['start_line']}-{node['end_line']})")
        print(f"    Type: {node['type']}")
        print(f"    Metadata: {node.get('metadata', 'N/A')}")
        print(f"    Code: \"{preview}...\"")
        print("-" * 60)
        count += 1

    # 4. EXPORT JSON COMPLETO
    if output_json:
        logger.info("\n--- EXPORT JSON ---")
        try:
            export_data = {
                "meta": {
                    "repo": repo_path,
                    "date": time.time(),
                    "stats": stats
                },
                "data": {
                    "files": files,       # <--- AGGIUNTO QUI
                    "nodes": nodes,
                    "edges": edges,
                    "contents": contents
                }
            }

            with open(output_json, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)

            size_mb = os.path.getsize(output_json) / (1024 * 1024)
            logger.info(f"✅ Dump salvato in: {output_json} ({size_mb:.2f} MB)")

        except Exception as e:
            logger.error(f"❌ Errore export: {e}")

    indexer.close()
    logger.info("\n[DONE]")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_path", type=str)
    parser.add_argument("--out", type=str, default="debug_full_dump.json")
    parser.add_argument("--branch", type=str, default="main")  # ADDED
    args = parser.parse_args()
    test_indexing_full(args.repo_path, args.out, branch=args.branch)
