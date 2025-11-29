from code_graph_indexer.retrieval.rankers import reciprocal_rank_fusion

def test_rrf_logic():
    # Setup dummy data
    candidates = {
        "A": {"id": "A", "rrf_ranks": {"vector": 0, "keyword": 10}}, # 1st in vector
        "B": {"id": "B", "rrf_ranks": {"keyword": 0}},               # 1st in keyword
        "C": {"id": "C", "rrf_ranks": {"vector": 1, "keyword": 1}}   # 2nd in both
    }
    
    results = reciprocal_rank_fusion(candidates, k=1)
    
    # "C" should win because it is consistent in both
    assert results[0]['id'] == "C"
    assert results[1]['id'] == "A"
