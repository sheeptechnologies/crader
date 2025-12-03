🧭 Code Navigator

Modulo: code_graph_indexer.navigator

Classe: CodeNavigator

Il Navigator permette di "camminare" sul grafo del codice. A differenza del Retriever (che cerca per somiglianza), il Navigator si muove seguendo relazioni deterministiche (Struttura e Chiamate). È essenziale per permettere agli Agenti di eseguire "Deep Dives" nel codice.

🎯 Scopo Principale

Navigazione Verticale (Scrolling): Permette di leggere il chunk precedente o successivo a quello corrente. Essenziale quando l'LLM ha bisogno di più contesto attorno a uno snippet.

Navigazione Gerarchica (Drill-Up): Permette di saltare dal metodo alla classe che lo contiene.

Impact Analysis (Incoming Refs): Risponde alla domanda "Se modifico questa funzione, chi si rompe?".

Pipeline Analysis (Outgoing Calls): Risponde alla domanda "Cosa fa internamente questa funzione?".

🚀 Utilizzo

Immagina che l'Agente abbia trovato un nodo interessante (node_id) tramite il Retriever.

from code_graph_indexer import CodeNavigator

nav = CodeNavigator(storage)
current_node_id = "uuid-del-nodo-trovato"

# 1. Contesto: Chi è il genitore?
parent = nav.read_parent_chunk(current_node_id)
print(f"Questo codice è dentro: {parent['type']} {parent['file_path']}")

# 2. Scrolling: Leggi il pezzo successivo
next_chunk = nav.read_neighbor_chunk(current_node_id, direction="next")
print(f"Codice seguente:\n{next_chunk['content']}")

# 3. Analisi Impatto: Chi chiama questa funzione?
callers = nav.analyze_impact(current_node_id)
print("Chiamanti:")
for ref in callers:
    print(f"- {ref['file']} alla riga {ref['line']}")

# 4. Esplorazione: Call Graph
graph = nav.visualize_pipeline(current_node_id, max_depth=2)
# Restituisce un albero JSON delle chiamate in uscita


🔌 API Reference

read_neighbor_chunk(node_id, direction="next") -> Optional[Dict]

Recupera il chunk adiacente nello stesso file.

direction: "next" o "prev".

Utile per superare i limiti della context window, leggendo il file a pezzi.

read_parent_chunk(node_id) -> Optional[Dict]

Restituisce il nodo che contiene quello corrente (es. Classe -> Metodo).

analyze_impact(node_id, limit=20) -> List[Dict]

Trova le Incoming References.

Restituisce una lista di file e righe che importano o invocano il nodo corrente.

Utilizza gli archi calls, imports, references del grafo.

visualize_pipeline(node_id, max_depth=2) -> Dict

Costruisce un albero delle Outgoing Calls.

Utile per capire la logica di business e il flusso di esecuzione a partire da un Entry Point.