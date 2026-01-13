import os

from debugger.server import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_read_main():
    response = client.get("/api/repos")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_add_repo_and_flow():
    # Use a small local repo (the current one)
    current_dir = os.getcwd()

    # 1. Add Repo
    response = client.post(
        "/api/repos",
        json={
            "path_or_url": current_dir,
            "name": "sheep-indexer-test",
            "branch": "main",  # Assuming main exists
        },
    )
    if response.status_code != 200:
        print(f"Add Repo Failed: {response.json()}")
    assert response.status_code == 200
    repo_id = response.json()["id"]
    print(f"Repo ID: {repo_id}")

    # 2. Index (Background task, so we can't easily wait for it in sync test without mocking or sleep)
    # But we can check if it accepts the request
    response = client.post(f"/api/repos/{repo_id}/index", json={"force": True})
    assert response.status_code == 200
    assert response.json()["status"] == "indexing_started"

    # 3. Check files (might be empty initially if indexing isn't done)
    # We won't wait for indexing here as it might take time.
    response = client.get(f"/api/repos/{repo_id}/files")
    assert response.status_code == 200

    print("Basic flow test passed!")


if __name__ == "__main__":
    test_read_main()
    try:
        test_add_repo_and_flow()
    except Exception as e:
        print(f"Test failed: {e}")
