from typing import Any, Dict, List


def reciprocal_rank_fusion(candidates: Dict[str, Dict[str, Any]], k: int = 60) -> List[Dict[str, Any]]:
    """
    Implements Reciprocal Rank Fusion (RRF) for result re-ranking.

    RRF is a robust, parameter-free method for combining ranked lists from different retrieval systems
    (e.g., Vector Search and Keyword Search). It privileges documents that appear consistently high
    in multiple lists, penalizing outliers that only appear in one.

    **Formula**:
    `Score(d) = Σ (1 / (k + rank_i(d)))` for each retrieval method `i`.

    Args:
        candidates (Dict): A dictionary of accumulated candidate documents.
                         Each item MUST have an 'rrf_ranks' dictionary field mapping strategy names to 0-based ranks.
                         Example: `{'doc1': {'rrf_ranks': {'vector': 0, 'keyword': 5}, ...}}`
        k (int): The smoothing constant (default: 60).
                 Higher values dampen the importance of high rankings, reducing the impact of outliers.

    Returns:
        List[Dict[str, Any]]: A flat list of document dictionaries, sorted by 'final_rrf_score' in descending order.
    """
    for nid, data in candidates.items():
        rrf_score = 0.0
        # 'rrf_ranks' maps the method (e.g., 'vector', 'keyword') to the rank (0-based)
        ranks = data.get("rrf_ranks", {})

        for method, rank in ranks.items():
            # The lower the rank (0=first), the higher the contribution
            rrf_score += 1.0 / (k + rank + 1)

        data["final_rrf_score"] = rrf_score

    # Sort by descending score
    sorted_docs = sorted(candidates.values(), key=lambda x: x.get("final_rrf_score", 0.0), reverse=True)

    return sorted_docs
