🏗️ Codebase Indexer

Modulo: code_graph_indexer.indexer

Classe: CodebaseIndexer

Il modulo Indexer è il motore di ingestione della libreria. È responsabile della trasformazione del codice sorgente grezzo in un Knowledge Graph strutturato e interrogabile. Gestisce l'intero ciclo di vita: parsing, analisi statica, costruzione del grafo e vettorizzazione.

🎯 Scopo Principale

Parsing Ibrido: Combina l'accuratezza sintattica di Tree-sitter con l'analisi semantica di SCIP (Source Code Indexing Protocol).

Incrementalità: Rileva automaticamente se una repository è già aggiornata (tramite hash dei commit Git) per evitare lavoro inutile.

Graph Construction: Crea nodi (Files, Chunks) e archi (Defines, Calls, Imports) nel database.

Embedding Orchestration: Coordina la generazione dei vettori per la ricerca semantica.

🚀 Utilizzo

Inizializzazione e Indicizzazione

from code_graph_indexer import CodebaseIndexer
from code_graph_indexer.storage.sqlite import SqliteGraphStorage

# 1. Setup Storage
storage = SqliteGraphStorage("production_index.db")

# 2. Setup Indexer
indexer = CodebaseIndexer(
    repo_path="/path/to/my/project",
    storage=storage
)

# 3. Esecuzione Pipeline (Parsing + Graph Build)
# force=True ignora il check del commit hash e re-indicizza tutto.
indexer.index(force=False)

print(f"Stats: {indexer.get_stats()}")


Generazione Embeddings

L'embedding è separato dall'indexing per modularità.

from code_graph_indexer.providers.embedding import FastEmbedProvider

provider = FastEmbedProvider()

# Genera vettori per i nodi che ne sono privi
# indexer.embed restituisce un generatore per monitorare il progresso
for status in indexer.embed(provider, batch_size=32):
    print(status)


⚙️ Come Funziona (Pipeline Interna)

Quando chiami .index(), avviene questa sequenza:

Context Detection: Recupera URL remoto, branch e commit hash correnti via Git.

Stale Check: Interroga il DB. Se last_commit nel DB coincide con quello su disco, l'indicizzazione viene saltata (a meno di force=True).

Tree-sitter Parsing: Scansiona i file supportati, divide il codice in chunk logici (Classi, Funzioni) e assegna tag semantici (es. api_endpoint, test_case).

SCIP Analysis: Lancia in background i tool SCIP (es. scip-python) per mappare riferimenti precisi (Cross-file navigation).

Graph Building: Unisce i chunk di Tree-sitter con le relazioni di SCIP e salva tutto su SQLite.

FTS Indexing: Popola la tabella virtuale per la ricerca Full-Text.

🔌 API Reference

__init__(repo_path: str, storage: GraphStorage)

Prepara l'indexer. Lancia ValueError se il path non esiste.

index(force: bool = False) -> None

Esegue la pipeline di indicizzazione.

force: Se True, cancella i dati precedenti per questa repo/branch e ricostruisce da zero.

embed(provider: EmbeddingProvider, batch_size: int = 32) -> Generator

Processa i nodi testuali e salva i vettori.

Yields: Dizionari di stato o documenti processati (se in debug).

get_stats() -> Dict

Restituisce conteggi rapidi (file, nodi, archi, vettori) per verificare la salute dell'indice.