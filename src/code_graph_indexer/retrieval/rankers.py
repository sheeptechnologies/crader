from typing import Dict, List, Any

def reciprocal_rank_fusion(candidates: Dict[str, Dict[str, Any]], k: int = 60) -> List[Dict[str, Any]]:
    """
    Implementa l'algoritmo Reciprocal Rank Fusion (RRF).
    
    Formula: Score = sum(1 / (k + rank_i)) per ogni metodo di retrieval.
    Questo approccio normalizza i punteggi di sistemi diversi (Vettoriale vs Keyword)
    basandosi puramente sulla posizione in classifica.
    
    Args:
        candidates: Dizionario di candidati accumulati dal Searcher.
                    Ogni item deve avere un campo 'rrf_ranks' (Dict[method, rank]).
        k: Costante di smoothing (default 60, come consigliato in letteratura).
           Valori più alti mitigano l'impatto dei documenti outlier.
    
    Returns:
        Lista di dizionari ordinata per score RRF decrescente.
    """
    for nid, data in candidates.items():
        rrf_score = 0.0
        # 'rrf_ranks' mappa il metodo (es. 'vector', 'keyword') al rank (0-based)
        ranks = data.get('rrf_ranks', {})
        
        for method, rank in ranks.items():
            # Più il rank è basso (0=primo), più alto è il contributo
            rrf_score += 1.0 / (k + rank + 1)
        
        data['final_rrf_score'] = rrf_score
        
    # Ordina per score decrescente
    sorted_docs = sorted(
        candidates.values(), 
        key=lambda x: x.get('final_rrf_score', 0.0), 
        reverse=True
    )
    
    return sorted_docs