from code_graph_indexer.retriever import CodeRetriever

def test_retriever_hybrid_flow(mock_storage, mock_embedder):
    # 1. Populate DB with dummy data (bypassing indexer)
    mock_storage.save_embeddings([{
        "id": "1", "chunk_id": "1", "text_content": "login function", 
        "vector": [0.1] * 384, "repo_id": "test_repo", "branch": "main"
    }])
    mock_storage.commit()

    # 2. Execute retriever
    retriever = CodeRetriever(mock_storage, mock_embedder)
    results = retriever.retrieve("login", repo_id="test_repo", branch="main")

    # 3. Assertions
    assert len(results) >= 1 # Might be more if FTS matches too?
    # Check if at least one result is our inserted one
    found = False
    for res in results:
        if res.node_id == "1":
            found = True
            break
    assert found
    
    # Verify that search_vectors was called
    mock_embedder.embed.assert_called_once()
