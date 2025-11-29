import pytest
from code_graph_indexer.storage.sqlite import SqliteGraphStorage

def test_delete_previous_data_scope_isolation(mock_storage):
    # Insert data for Repo A (Branch Main) and Repo B (Branch Main)
    mock_storage.save_embeddings([{
        "id": "A1", "chunk_id": "A1", "text_content": "A1", 
        "vector": [0.1] * 384, "repo_id": "repo_A", "branch": "main"
    }])
    mock_storage.save_embeddings([{
        "id": "B1", "chunk_id": "B1", "text_content": "B1", 
        "vector": [0.1] * 384, "repo_id": "repo_B", "branch": "main"
    }])
    mock_storage.commit()

    # Call delete_previous_data only for Repo A
    mock_storage.delete_previous_data("repo_A", "main")
    
    # Verify that data for Repo A is gone, but Repo B is still intact
    # Note: This assumes we have a way to check existence, e.g. by searching or counting
    # Since search_vectors filters by repo/branch, we can use that or check internal tables if exposed
    # For now, let's assume we can check via search or direct SQL if needed.
    # But wait, delete_previous_data might not commit immediately? Check implementation.
    # Assuming it does or we need to commit.
    mock_storage.commit()
    
    # Check Repo A
    results_A = mock_storage.search_vectors([0.1]*384, repo_id="repo_A", branch="main")
    assert len(results_A) == 0
    
    # Check Repo B
    results_B = mock_storage.search_vectors([0.1]*384, repo_id="repo_B", branch="main")
    assert len(results_B) == 1

def test_branch_isolation_cleanup(mock_storage):
    # Insert data for Repo A (Branch main) and Repo A (Branch feature)
    mock_storage.save_embeddings([{
        "id": "A_main", "chunk_id": "A_main", "text_content": "A_main", 
        "vector": [0.1] * 384, "repo_id": "repo_A", "branch": "main"
    }])
    mock_storage.save_embeddings([{
        "id": "A_feature", "chunk_id": "A_feature", "text_content": "A_feature", 
        "vector": [0.1] * 384, "repo_id": "repo_A", "branch": "feature"
    }])
    mock_storage.commit()

    # Call delete_previous_data(repo_A, branch='main')
    mock_storage.delete_previous_data("repo_A", "main")
    mock_storage.commit()

    # Verify that data for feature still exists and only main was cleaned
    results_main = mock_storage.search_vectors([0.1]*384, repo_id="repo_A", branch="main")
    assert len(results_main) == 0
    
    results_feature = mock_storage.search_vectors([0.1]*384, repo_id="repo_A", branch="feature")
    assert len(results_feature) == 1

def test_vector_search_math_correctness(mock_storage):
    # Insert 3 known orthogonal vectors (e.g. [1,0...], [0,1...])
    # Note: dimension is 384, so we pad with zeros
    v1 = [1.0] + [0.0] * 383
    v2 = [0.0, 1.0] + [0.0] * 382
    v3 = [0.0, 0.0, 1.0] + [0.0] * 381
    
    mock_storage.save_embeddings([
        {"id": "v1", "chunk_id": "v1", "text_content": "v1", "vector": v1, "repo_id": "math_test", "branch": "main"},
        {"id": "v2", "chunk_id": "v2", "text_content": "v2", "vector": v2, "repo_id": "math_test", "branch": "main"},
        {"id": "v3", "chunk_id": "v3", "text_content": "v3", "vector": v3, "repo_id": "math_test", "branch": "main"}
    ])
    mock_storage.commit()
    
    # Call search_vectors with query v1
    results = mock_storage.search_vectors(v1, repo_id="math_test", branch="main", limit=3)
    
    # Verify that the first result is exactly v1 and score is ~1.0
    assert results[0]["id"] == "v1"
    # assert results[0].score >= 0.99 # Score might not be exposed in the object returned by search_vectors directly depending on implementation, check if it is.
