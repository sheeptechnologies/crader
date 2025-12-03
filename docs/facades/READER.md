📖 Code Reader

Modulo: code_graph_indexer.reader

Classe: CodeReader

Il Reader fornisce un accesso sicuro e controllato al file system fisico della repository. È lo strumento che un Agente usa quando ha bisogno di leggere il contenuto completo di un file (dopo averlo trovato col Retriever) o di esplorare la struttura delle cartelle.

🎯 Scopo Principale

Security (Anti-Path Traversal): Impedisce all'Agente di leggere file fuori dalla repository (es. ../../etc/passwd). Risolve i path relativi rispetto alla root della repo indicizzata.

Context-Awareness: Utilizza il repo_id per risolvere automaticamente il percorso fisico su disco (utile se le repo sono in volumi Docker montati dinamicamente).

Safety: Protegge da letture di file binari o file eccessivamente grandi che potrebbero intasare la context window dell'LLM.

🚀 Utilizzo

from code_graph_indexer import CodeReader

reader = CodeReader(storage)
repo_id = "..."

# 1. Listare i file in una cartella
files = reader.list_directory(repo_id, "src/utils")
for f in files:
    print(f"{'[DIR]' if f['type']=='dir' else '[FILE]'} {f['name']}")

# 2. Leggere un file specifico
try:
    file_data = reader.read_file(repo_id, "src/utils/helpers.py")
    print(file_data['content'])
    
    # 3. Leggere solo un range di righe (per risparmiare token)
    snippet = reader.read_file(repo_id, "src/main.py", start_line=10, end_line=20)
except ValueError as e:
    print(f"Errore: {e}")


🔌 API Reference

read_file(repo_id, file_path, start_line=None, end_line=None) -> Dict

Legge un file di testo.

Controlli: Verifica che il file esista, sia dentro la repo, e sia < 10MB.

Return:

{
    "file_path": "src/main.py",
    "content": "def main(): ...",
    "start_line": 1,
    "end_line": 50,
    "size_bytes": 1024
}


list_directory(repo_id, path) -> List[Dict]

Elenca il contenuto di una directory.

Return: Lista ordinata (Directory prime, poi File A-Z).

[
    {"name": "utils", "type": "dir", "path": "src/utils"},
    {"name": "app.py", "type": "file", "path": "src/app.py"}
]
