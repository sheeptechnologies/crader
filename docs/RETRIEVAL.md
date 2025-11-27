# Retrieval Engine Architecture

Questo documento descrive l'architettura del modulo `code_graph_indexer.retrieval`, il motore di ricerca progettato per abilitare scenari di Agentic RAG su codebase complesse.

## 1. Obiettivi

A differenza di un sistema RAG generico (che recupera testo basandosi solo sulla similarità semantica), il **Code Retriever** deve:

- **Capire la Struttura**: non restituire solo snippet isolati, ma fornire il contesto gerarchico (Classe, Modulo).
- **Essere Preciso**: trovare identificatori esatti (es. nomi di variabili, codici di errore) che i vettori tendono a "sfocare".
- **Navigare le Relazioni**: permettere all’agente di esplorare chi chiama una funzione o quali dipendenze usa.

## 2. Architettura Logica

Il sistema di retrieval è composto da tre layer principali:

```mermaid
graph TD
    UserQuery --> QueryAnalysis
    QueryAnalysis --> HybridSearcher

    subgraph HybridSearcher
        VectorSearch[Dense Search (FastEmbed)]
        KeywordSearch[Sparse Search (FTS5)]
    end

    VectorSearch --> Reranker
    KeywordSearch --> Reranker
    Reranker --> TopKResults

    TopKResults --> GraphExpander
    GraphExpander --> FinalContext
```

### Componenti

- **CodeRetriever**: il facade principale che orchestra il processo.
- **HybridSearcher**: esegue ricerche parallele (Vettoriale + Keyword).
- **Reranker**: fonde i risultati usando algoritmi di ranking (RRF).
- **GraphWalker**: espande i nodi trovati navigando il grafo strutturale e semantico.

## 3. Strategie di Ricerca

Il sistema implementa una strategia **Hybrid Search** che combina semantic e sparse retrieval.

### A. Semantic Search (Dense Retrieval)

**Tecnologia**: Cosine Similarity sui vettori generati da `jina-embeddings-v2-base-code`.  
**Scopo**: catturare concetti e intenzioni.  
**Esempio Query**: “Logica per la gestione dei retry nel database”.

**Implementation Detail**:
- SQLite: caricamento vettori in-memory e similarity via numpy.
- Postgres: uso nativo di `pgvector` con operatore `<=>`.

### B. Keyword Search (Sparse Retrieval)

**Tecnologia**: SQLite FTS5 con tokenizzazione trigram.  
**Scopo**: intercettare identificatori esatti.  
**Esempio Query**: `Error_503_ServiceUnavailable` o `process_payment_v2`.

### C. Metadata Filtering (Pre-filtering)

Filtri rigidi sui metadati in `VectorDocument`:

- `repo_id`
- `language`
- `directory`
- `chunk_type`

## 4. Reranking (Reciprocal Rank Fusion)

I risultati delle due ricerche vengono fusi usando RRF.

Formula:

```
Score(d) = Σ_r 1 / (k + rank_r(d))
```

Documenti presenti in entrambe le liste → punteggio elevato.

## 5. Graph Expansion (Context Enrichment)

Si arricchiscono i nodi “Gold” navigando il grafo.

### Vertical Expansion (Gerarchia)

Risale `child_of` → include classe, modulo, import rilevanti.

### Horizontal Expansion (Relazioni SCIP)

Analizza relazioni `calls`:

- **Incoming Calls**: chi usa questa funzione?
- **Outgoing Calls**: cosa usa questa funzione?

## 6. Data Model (RetrievedContext)

```python
@dataclass
class RetrievedContext:
    node_id: str
    file_path: str

    content: str
    score: float
    type: str
    start_line: int
    end_line: int

    context_header: Optional[str] = None
    related_definitions: List[str] = field(default_factory=list)
```

## 7. Roadmap di Sviluppo

1. Storage Update: implementare `search_fts` e `fetch_vectors`.
2. Search Logic: implementare HybridSearcher (numpy).
3. Ranking: implementare RRF.
4. Graph Walker: navigazione edges.
5. Integration: esporre `indexer.retrieve(query)` o creare client dedicato.
