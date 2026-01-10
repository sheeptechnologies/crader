import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
# Hack to avoid importing tree-sitter
sys.modules["tree_sitter"] = type("Mock", (object,), {"Parser": None, "Node": None})
sys.modules["tree_sitter_languages"] = type("Mock", (object,), {"get_language": None})

from src.crader.storage.postgres import PostgresGraphStorage

DB_URL = os.getenv("SHEEP_DB_URL", "postgresql://sheep_user:sheep_password@localhost:5433/sheep_index")


def get_storage():
    return PostgresGraphStorage(DB_URL, vector_dim=1536)


def check_repo(repo_id, file_path):
    print(f"Checking repo {repo_id}...")
    storage = get_storage()
    repo = storage.get_repository(repo_id)

    if not repo:
        print("❌ Repo not found in DB")
        return

    print(f"✅ Repo found: {repo['name']}")
    print(f"📍 Local Path: {repo['local_path']}")

    full_path = os.path.join(repo["local_path"], file_path)
    print(f"📂 Checking file: {full_path}")

    if os.path.exists(full_path):
        print("✅ File exists on disk")
    else:
        print("❌ File NOT found on disk")
        # List dir to see what's there
        parent = os.path.dirname(full_path)
        if os.path.exists(parent):
            print(f"Contents of {parent}:")
            print(os.listdir(parent))
        else:
            print(f"Parent dir {parent} does not exist")


def list_repos():
    storage = get_storage()
    with storage.pool.connection() as conn:
        rows = conn.execute("SELECT id, name, local_path FROM repositories").fetchall()
        print(f"Found {len(rows)} repositories:")
        for row in rows:
            print(f"ID: {row['id']} | Name: {row['name']} | Path: {row['local_path']}")


def check_schema():
    storage = get_storage()
    with storage.pool.connection() as conn:
        # Check columns in files table
        rows = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'files'"
        ).fetchall()
        print("Files Table Schema:")
        for row in rows:
            print(f"{row['column_name']}: {row['data_type']}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        list_repos()
    elif len(sys.argv) > 1 and sys.argv[1] == "schema":
        check_schema()
    elif len(sys.argv) < 3:
        print("Usage: python debugger/debug_db.py <repo_id> <file_path>")
        print("       python debugger/debug_db.py list")
        print("       python debugger/debug_db.py schema")
    else:
        check_repo(sys.argv[1], sys.argv[2])
