🐑 Code Graph Indexer - Enterprise Documentation

Versione: 1.0.0

Stato: Production Ready (Preview)

Lingua: Python 3.11+

📑 Indice

Panoramica

Installazione e Prerequisiti

Quick Start

Architettura del Sistema

Core Components (Module Reference)

Advanced Concepts

Database Schema

1. Panoramica

code_graph_indexer è un motore di indicizzazione semantica progettato per abilitare scenari di Agentic RAG (Retrieval-Augmented Generation) su codebase complesse.

A differenza dei tradizionali sistemi RAG che trattano il codice come semplice testo, questa libreria costruisce un Knowledge Graph che combina:

Analisi Sintattica (AST): Tramite Tree-sitter, per comprendere la struttura gerarchica (Classi, Metodi, Funzioni).

Analisi Semantica (LSIF/SCIP): Per mappare le relazioni precise di definizione e utilizzo (chi chiama chi, dove è definito un simbolo).

Vettori Semantici: Per la ricerca concettuale tramite embeddings.

Value Proposition

Navigazione Reale: Permette agli agenti AI di "cliccare" su definizioni e riferimenti come in un IDE.

Ricerca Ibrida: Combina la precisione della ricerca per keyword (FTS) con la flessibilità dei vettori.

Onboarding Veloce: Pipeline automatizzata che trasforma una cartella Git in un grafo interrogabile in pochi minuti.

2. Installazione e Prerequisiti

Requisiti di Sistema

Python: 3.11 o superiore.

SCIP Indexers: Per l'analisi avanzata delle relazioni, è necessario installare i tool SCIP specifici per i linguaggi target (es. scip-python, scip-typescript).

Git: Deve essere installato e accessibile nel PATH.

Installazione Libreria

# Clone del repository
git clone [https://github.com/your-org/code-graph-indexer.git](https://github.com/your-org/code-graph-indexer.git)
cd code-graph-indexer

# Installazione dipendenze
pip install -r requirements.txt


Setup SCIP (Opzionale ma Raccomandato)

Per abilitare il "Jump to Definition" preciso:

# Per Python
npm install -g @sourcegraph/scip-python

# Per TypeScript/JavaScript
npm install -g @sourcegraph/scip-typescript


3. Quick Start

Ecco come indicizzare una repository locale ed eseguire una ricerca semantica in meno di 30 righe di codice.

Fase 1: Indicizzazione

import os
from code_graph_indexer import CodebaseIndexer
from code_graph_indexer.storage.sqlite import SqliteGraphStorage

# 1. Configura lo storage (SQLite locale)
storage = SqliteGraphStorage("knowledge_graph.db")

# 2. Inizializza l'indexer puntando alla root della tua repository
repo_path = os.path.abspath("./my-project-src")
indexer = CodebaseIndexer(repo_path, storage)

# 3. Esegui la pipeline (Parsing -> Graph -> Storage)
indexer.index()

# 4. Ottieni l'ID univoco della repository (serve per le query)
repo_id = indexer.parser.repo_id
print(f"✅ Repository indicizzata. ID: {repo_id}")


Fase 2: Embedding

from code_graph_indexer.providers.embedding import FastEmbedProvider

# 1. Scegli un provider (FastEmbed gira locale su CPU)
provider = FastEmbedProvider(model_name="jinaai/jina-embeddings-v2-base-code")

# 2. Genera e salva gli embeddings
# Nota: indexer.embed è un generatore, usiamo list() per consumarlo tutto.
list(indexer.embed(provider, batch_size=16))


Fase 3: Ricerca (Retrieval)

from code_graph_indexer import CodeRetriever

# 1. Inizializza il Retriever
retriever = CodeRetriever(storage, provider)

# 2. Esegui una ricerca ibrida
query = "Come viene gestita l'autenticazione utente?"
results = retriever.retrieve(query, repo_id=repo_id, limit=5, strategy="hybrid")

# 3. Visualizza i risultati
for res in results:
    print(f"\n📄 File: {res.file_path} (L{res.start_line}-{res.end_line})")
    print(f"⭐ Score: {res.score:.4f}")
    print(f"🏷️  Tipo: {', '.join(res.semantic_labels)}")
    print("-" * 40)
    print(res.content[:200] + "...")


4. Architettura del Sistema

Il sistema opera attraverso una pipeline sequenziale divisa in quattro stadi principali.

graph TD
    A[Source Code] --> B(Parser Engine);
    A --> C(SCIP Engine);
    
    subgraph "1. Indexing Phase"
        B -->|AST Chunks| D[Knowledge Graph Builder];
        C -->|Relations| D;
        D --> E[(Graph Storage)];
    end
    
    subgraph "2. Embedding Phase"
        E -->|Nodes| F[Embedder];
        F -->|Vector| E;
    end
    
    subgraph "3. Retrieval Phase"
        G[User Query] --> H{Hybrid Search};
        H -->|Vectors| E;
        H -->|Keywords| E;
        E -->|Candidates| I[Reranker];
        I -->|Top K| L[Graph Walker];
        L --> M[Enriched Context];
    end


1. Indexing (Parsing & Graph Construction)

Tree-sitter: Divide il codice in "Chunk" logici (funzioni, classi).

SCIP: Analizza il codice per estrarre relazioni precise (definitions, references).

Graph Builder: Unisce i nodi e gli archi nel DB.

2. Embedding

Arricchisce ogni nodo con un vettore numerico per la ricerca semantica.

3. Retrieval

Usa Hybrid Search (Dense + Sparse) e Reciprocal Rank Fusion (RRF).

Esegue una Graph Walk finale per includere contesto extra.

5. Core Components (Module Reference)

La libreria è divisa in 4 moduli principali. Clicca sui link per la documentazione dettagliata di ciascun componente.

🏗️ CodebaseIndexer

Il motore di ingestione.

Gestisce il parsing sintattico (Tree-sitter) e semantico (SCIP).

Coordina la costruzione del Knowledge Graph e la generazione degli embeddings.

Vai alla documentazione Indexer ->

🔎 CodeRetriever

L'interfaccia di ricerca per l'Agente.

Esegue ricerche ibride (Keyword + Vettoriale).

Applica algoritmi di Reranking (RRF).

Arricchisce i risultati navigando il grafo per fornire contesto (Parent Context, Dependencies).

Vai alla documentazione Retriever ->

📖 CodeReader

Accesso sicuro al filesystem.

Permette la lettura del contenuto grezzo dei file.

Gestisce la sicurezza (Path Traversal Protection) e la risoluzione dei percorsi fisici.

Vai alla documentazione Reader ->

🧭 CodeNavigator

Esplorazione strutturale del grafo.

Permette "Deep Dives" nel codice.

Supporta navigazione verticale (Scroll Next/Prev), gerarchica (Parent/Child) e relazionale (Impact Analysis/Call Graph).

Vai alla documentazione Navigator ->

6. Advanced Concepts

Hybrid Search & RRF

La ricerca ibrida risolve due problemi opposti:

Semantic Gap: I vettori capiscono che "auth" è simile a "login", ma falliscono su codici esatti come Error_503.

Lexical Gap: La ricerca keyword trova Error_503 ma non capisce che "user verification" è "auth".

Il sistema esegue entrambe le ricerche in parallelo e fonde i risultati con RRF:
$$ Score(d) = \sum_{r \in \text{strategies}} \frac{1}{k + rank_r(d)} $$
Dove $k$ è una costante di smoothing (default 60).

Graph Expansion

Un risultato di ricerca grezzo è spesso privo di contesto. Il GraphWalker arricchisce ogni risultato prima di restituirlo all'LLM:

Vertical Context: "Questo snippet è dentro la classe AuthManager nel file security.py".

Horizontal Context: "Questo snippet chiama le funzioni validate_token e db_connect".

7. Database Schema

La persistenza è gestita su SQLite per portabilità e velocità.

Tabella nodes

Colonna

Tipo

Descrizione

id

TEXT

UUID del nodo.

file_path

TEXT

Path relativo del file sorgente.

chunk_hash

TEXT

Hash del contenuto (per deduplica).

start_line

INT

Riga inizio.

end_line

INT

Riga fine.

metadata_json

TEXT

JSON con tag semantici (es. ruolo, tipo).

Tabella edges

Colonna

Tipo

Descrizione

source_id

TEXT

ID nodo sorgente.

target_id

TEXT

ID nodo destinazione.

relation_type

TEXT

calls, defines, imports, child_of.

Tabella node_embeddings

Colonna

Tipo

Descrizione

chunk_id

TEXT

FK su nodes.id.

embedding

BLOB

Vettore binario (float32).

vector_hash

TEXT

Hash del testo embeddato (per evitare ricalcoli).