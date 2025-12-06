import os
import sys
import uuid
import json
import tempfile
import subprocess
import shutil
import atexit
from typing import Optional, List, Union

# --- CONFIGURAZIONE PATH ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, "..", "src"))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

# --- ENV ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

from code_graph_indexer import CodebaseIndexer, CodeRetriever, CodeReader, CodeNavigator
from code_graph_indexer.storage.postgres import PostgresGraphStorage
from code_graph_indexer.schema import VALID_ROLES, VALID_CATEGORIES

try:
    from code_graph_indexer.providers.openai_emb import OpenAIEmbeddingProvider
except ImportError:
    from code_graph_indexer.providers.embedding import OpenAIEmbeddingProvider

# ==============================================================================
# 1. SETUP SISTEMA (Clone Flask + Postgres)
# ==============================================================================

DB_PORT = "5433" 
DB_URL = f"postgresql://sheep_user:sheep_password@localhost:{DB_PORT}/sheep_index"

# --- CLONE FLASK ---
REPO_URL = "https://github.com/pallets/flask.git"
REPO_PATH = tempfile.mkdtemp(prefix="sheep_agent_flask_")

def cleanup_temp_dir():
    if os.path.exists(REPO_PATH):
        print(f"\n🧹 Pulizia directory temporanea: {REPO_PATH}")
        shutil.rmtree(REPO_PATH)

atexit.register(cleanup_temp_dir)

print(f"🔄 Cloning Flask ({REPO_URL}) into {REPO_PATH}...")
try:
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", "main", REPO_URL, REPO_PATH],
        check=True,
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE
    )
    print("✅ Clone completato.")
except subprocess.CalledProcessError as e:
    print(f"❌ Errore durante il clone: {e}")
    sys.exit(1)

print(f"🐘 Connecting to: {DB_URL}")

try:
    storage = PostgresGraphStorage(DB_URL, vector_dim=1536)
    provider = OpenAIEmbeddingProvider(model="text-embedding-3-small")
except Exception as e:
    print(f"❌ Errore Setup Infrastruttura: {e}")
    sys.exit(1)

# --- LOGICA INDEXING & EMBEDDING ---
try:
    indexer = CodebaseIndexer(REPO_PATH, storage)
    repo_info = indexer.parser.metadata_provider.get_repo_info()
    url = repo_info['url']
    branch = repo_info['branch']
    print(f"📂 Repository Context: {url} (Branch: {branch})")

    # 1. Verifica/Esecuzione PARSING (Struttura)
    existing_record = storage.get_repository_by_context(url, branch)
    
    if not existing_record or existing_record.get('status') != 'completed':
        print("\n🚀 REPO NON INDICIZZATA (Parsing mancante). Avvio Indexing Strutturale...")
        try:
            indexer.index(force=True)
            print("✅ Parsing completato.")
            # Aggiorniamo il record dopo l'indexing
            existing_record = storage.get_repository_by_context(url, branch)
        except Exception as e:
            print(f"❌ Errore durante Indexing: {e}")
            sys.exit(1)
    else:
        print("✅ Struttura repo già presente (Parsing Cache Hit).")

    # 2. Verifica/Esecuzione EMBEDDING (Vettori) - ESEGUITO SEMPRE
    # L'embedder controlla internamente quali nodi mancano e calcola solo quelli.
    print("🤖 Verifica stato Embeddings...")
    try:
        # Consumiamo il generatore. Se tutto è già fatto, finirà subito.
        count = 0
        for _ in indexer.embed(provider, batch_size=100):
            count += 1
            if count % 5 == 0: print(".", end="", flush=True)
        print("\n✅ Embedding sincronizzato.")
    except Exception as e:
        print(f"⚠️ Errore non bloccante durante Embedding: {e}")

    CURRENT_REPO_ID = str(existing_record['id'])
    print(f"🔑 Session Repo ID: {CURRENT_REPO_ID}")

except Exception as e:
    print(f"⚠️ Errore critico inizializzazione: {e}")
    sys.exit(1)

# Facade
retriever = CodeRetriever(storage, provider)
reader = CodeReader(storage)
navigator = CodeNavigator(storage)

print(f"✅ Sistema Inizializzato. Pronti per le domande su Flask.")

# ==============================================================================
# 2. DEFINIZIONE TOOLS E AGENT (Invariato)
# ==============================================================================

class SearchFiltersInput(BaseModel):
    path_prefix: Optional[Union[str, List[str]]] = Field(None, description="Filtra per cartella.")
    language: Optional[Union[str, List[str]]] = Field(None, description="Filtra per linguaggio.")
    role: Optional[Union[VALID_ROLES, List[VALID_ROLES]]] = Field(None, description="Include ruoli specifici.")
    exclude_role: Optional[Union[VALID_ROLES, List[VALID_ROLES]]] = Field(None, description="Esclude ruoli.")
    category: Optional[Union[VALID_CATEGORIES, List[VALID_CATEGORIES]]] = Field(None, description="Include categorie.")
    exclude_category: Optional[Union[VALID_CATEGORIES, List[VALID_CATEGORIES]]] = Field(None, description="Esclude categorie.")

@tool
def search_codebase(query: str, filters: Optional[SearchFiltersInput] = None):
    """Cerca semanticamente nel codice."""
    filter_dict = filters.model_dump(exclude_none=True) if filters else None
    try:
        results = retriever.retrieve(query, repo_id=CURRENT_REPO_ID, limit=5, strategy="hybrid", filters=filter_dict)
        return "\n".join([r.render() for r in results]) if results else "Nessun risultato trovato."
    except Exception as e:
        return f"Errore ricerca: {e}"

@tool
def read_file_content(file_path: str, start_line: Optional[int] = None, end_line: Optional[int] = None):
    """Legge contenuto file."""
    try:
        data = reader.read_file(CURRENT_REPO_ID, file_path, start_line, end_line)
        return f"File: {data['file_path']}\nContent:\n{data['content']}"
    except Exception as e:
        return f"Errore lettura: {e}"

@tool
def inspect_node_relationships(node_id: str):
    """
    Analizza le relazioni (Parent, Next, Callers, Calls) di un Chunk ID.
    Usa questo dopo aver trovato un ID con la ricerca.
    
    IMPORTANTE: node_id DEVE essere un UUID (es. '3fa85f64-5717...'), NON un file path.
    Trovi l'UUID nell'output di `search_codebase`.
    """
    # [FIX] Validazione Input: L'Agente a volte manda path invece di UUID
    try:
        uuid.UUID(node_id)
    except ValueError:
        return (
            f"ERRORE: '{node_id}' non è un ID valido. "
            "Hai passato un file path? Devi passare il 'node_id' (UUID) "
            "che trovi nei risultati di `search_codebase` (es. 'a1b2c3d4-....')."
        )

    report = []
    try:
        # 1. Parent
        parent = navigator.read_parent_chunk(node_id)
        if parent: 
            report.append(f"⬆️ PARENT: {parent.get('type')} in {parent.get('file_path')}")
        else:
            report.append("⬆️ PARENT: None (Top-level node)")

        # 2. Next
        nxt = navigator.read_neighbor_chunk(node_id, "next")
        if nxt: 
            prev = nxt.get('content', '').split('\n')[0][:80]
            report.append(f"➡️ NEXT: {nxt.get('type')} (ID: {nxt.get('id')})\n   Preview: {prev}...")
            
        # 3. Impact
        impact = navigator.analyze_impact(node_id)
        if impact:
            report.append(f"⬅️ CALLED BY ({len(impact)} refs):")
            for i in impact[:5]:
                report.append(f"   - {i['file']} L{i['line']} ({i['relation']})")
        else:
            report.append("⬅️ CALLED BY: None")
            
        # 4. Pipeline
        pipe = navigator.visualize_pipeline(node_id)
        if pipe and pipe.get("call_graph"):
            report.append(f"⤵️ CALLS: {json.dumps(pipe['call_graph'], indent=2)}")
        else:
            report.append("⤵️ CALLS: None")
            
        return "\n".join(report)
    
    except Exception as e: 
        return f"Errore interno ispezione: {e}"
    
@tool
def find_folder(name_pattern: str):
    """
    Cerca il percorso di una cartella dato un nome parziale.
    Usa questo se `list_repo_structure` fallisce perché non trovi la cartella prevista.
    Es: cerchi "flask" -> trova "src/flask".
    """
    try:
        dirs = reader.find_directories(CURRENT_REPO_ID, name_pattern)
        if not dirs:
            return f"Nessuna cartella trovata contenente '{name_pattern}'."
        return "Cartelle trovate:\n" + "\n".join([f"- {d}" for d in dirs])
    except Exception as e:
        return f"Errore ricerca cartelle: {e}"
    
@tool
def list_repo_structure(path: str = "", max_depth: int = 2):
    """
    Elenca file e cartelle nella repository. 
    Usa questo tool ALL'INIZIO per capire com'è organizzato il progetto (es. dove sono i source file, dove sono i test).
    """
    try:
        items = reader.list_directory(CURRENT_REPO_ID, path)
        output = [f"Listing '{path or '/'}':"]
        for item in items:
            icon = "📁" if item['type'] == 'dir' else "📄"
            output.append(f"{icon} {item['name']}")
            
            # Mini-esplorazione per profondità 2
            if item['type'] == 'dir' and max_depth > 1:
                try:
                    sub_items = reader.list_directory(CURRENT_REPO_ID, item['path'])
                    # Mostra solo i primi 5 file per non intasare
                    for i, sub in enumerate(sub_items):
                        if i >= 5: 
                            output.append(f"  └─ ... ({len(sub_items)-5} more)")
                            break
                        sub_icon = "  └─ 📁" if sub['type'] == 'dir' else "  └─ 📄"
                        output.append(f"{sub_icon} {sub['name']}")
                except: pass
        return "\n".join(output)
    except Exception as e: return f"Errore listing: {e}"


tools = [search_codebase, read_file_content, inspect_node_relationships, list_repo_structure,find_folder]

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
SYSTEM_PROMPT = "Sei un Senior Software Engineer esperto su Flask. Rispondi usando SOLO i tool."
checkpointer = MemorySaver()
agent_executor = create_react_agent(llm, tools, checkpointer=checkpointer)

def chat_loop():
    print("\n🤖 AGENT READY (LangGraph) -> Flask Repo. Type 'exit' to quit.")
    print("-" * 60)
    config = {"configurable": {"thread_id": "session_flask_v2"}}
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.lower() in {"exit", "quit"}: break
        print("\nThinking...", end="", flush=True)
        try:
            events = agent_executor.stream({"messages": [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_input)]}, config=config, stream_mode="values")
            print("\r", end="")
            for event in events:
                if "messages" not in event: continue
                last_msg = event["messages"][-1]
                if getattr(last_msg, "type", None) == "ai":
                    if getattr(last_msg, "tool_calls", None):
                        for tc in last_msg.tool_calls: print(f"🛠️  Calling: {tc.get('name')} ({tc.get('args')})")
                    else:
                        print(f"🤖 Agent:\n{last_msg.content}\n")
        except Exception as e: print(f"\n❌ Errore agente: {e}")

if __name__ == "__main__":
    chat_loop()