🔎 Code Retriever

Modulo: code_graph_indexer.retriever

Classe: CodeRetriever

Il Retriever è la facciata di lettura progettata per gli Agenti AI (LLM). Non si limita a trovare pezzi di codice simili, ma costruisce un contesto arricchito che aiuta l'LLM a comprendere dove si trova il codice e cosa fa.

🎯 Scopo Principale

Hybrid Search: Risolve il problema del "Lessico vs Semantica".

Keyword Search: Trova nomi di variabili esatti, ID di errore, codici specifici (es. Error_502).

Vector Search: Trova concetti (es. "Logica di autenticazione" trova login.py).

Context Enrichment: Un chunk di codice da solo è inutile. Il Retriever aggiunge automaticamente informazioni sul genitore (Classe/Modulo) e sulle dipendenze.

Isolation: Garantisce che la ricerca avvenga solo all'interno della Repository (e Branch) specificati.

🚀 Utilizzo

from code_graph_indexer import CodeRetriever

# Assumendo storage e provider già configurati
retriever = CodeRetriever(storage, provider)

repo_id = "..." # ID ottenuto dall'Indexer

# Esempio 1: Ricerca generica
results = retriever.retrieve(
    query="Come gestiamo i retry del database?",
    repo_id=repo_id,
    limit=5
)

# Esempio 2: Ricerca con Filtri Avanzati
results = retriever.retrieve(
    query="Funzione di login",
    repo_id=repo_id,
    filters={
        "language": ["python"],
        "exclude_category": ["test"], # Ignora i file di test
        "path_prefix": "src/auth"     # Cerca solo in questa cartella
    }
)


🧠 Logica di Retrieval (RRF)

Il metodo retrieve utilizza l'algoritmo Reciprocal Rank Fusion (RRF) per combinare i risultati:

Esegue Vector Search (Top K*2 risultati).

Esegue Keyword Search (Top K*2 risultati).

Assegna un punteggio RRF ad ogni documento univo:
$$ Score = \sum \frac{1}{k + rank} $$

Riordina e prende i Top K.

Passa i risultati al Graph Walker per espandere il contesto (aggiunge parent_context e outgoing_definitions).

🔌 API Reference

retrieve(...) -> List[RetrievedContext]

Argomenti:

query (str): La domanda in linguaggio naturale o codice.

repo_id (str): Obbligatorio. L'ID della repo su cui cercare.

limit (int): Numero massimo di risultati (default 10).

strategy (str):

"hybrid" (Default): Vettoriale + Keyword + RRF.

"vector": Solo semantica.

"keyword": Solo BM25/Trigram.

filters (dict):

language: Lista estensioni (es. ["python", "js"]).

path_prefix: Filtra per cartella (es. src/models).

role: Filtra per ruolo semantico (es. entry_point, api_endpoint).

exclude_category: Esclude categorie (es. test, docs).

Oggetto RetrievedContext

L'oggetto restituito contiene:

content: Il codice sorgente.

semantic_labels: Cos'è questo codice? (es. ["API Route Handler"]).

parent_context: Dove si trova? (es. Inside class AuthController).

outgoing_definitions: Cosa usa? (es. Uses: verify_token, db_connect).