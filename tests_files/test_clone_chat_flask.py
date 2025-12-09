import os
import sys
import uuid
import json
import tempfile
import subprocess
import shutil
import atexit
import traceback
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

# Import componenti
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

# Configurazione DB (Verifica porta e credenziali)
DB_PORT = "5433" 
DB_URL = f"postgresql://sheep_user:sheep_password@localhost:{DB_PORT}/sheep_index"

# --- CLONE REPO ---
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

# --- GLOBAL SESSION VARIABLES ---
CURRENT_REPO_ID = None
CURRENT_SNAPSHOT_ID = None

# --- LOGICA INDEXING & EMBEDDING ---
try:
    indexer = CodebaseIndexer(REPO_PATH, storage)
    repo_meta = indexer.parser.metadata_provider.get_repo_info()
    print(f"📂 Context: {repo_meta['url']} (Branch: {repo_meta['branch']})")

    # 1. INDEXING (Blue-Green)
    # index() ora ritorna direttamente lo snapshot_id attivo
    print("\n🚀 Avvio Indexing (Snapshot Check)...")
    try:
        # force=False: Se esiste già, usa quello. force=True: Ricrea.
        CURRENT_SNAPSHOT_ID = indexer.index(force=False) 
        print(f"✅ Indexing completato. Snapshot ID: {CURRENT_SNAPSHOT_ID}")
    except Exception as e:
        print(f"❌ Errore durante Indexing: {e}")
        traceback.print_exc()
        sys.exit(1)

    # 2. SESSION PINNING
    # Recuperiamo anche repo_id per compatibilità (Identity)
    CURRENT_REPO_ID = storage.ensure_repository(repo_meta['url'], repo_meta['branch'], repo_meta['name'])
    
    if not CURRENT_SNAPSHOT_ID:
        # Fallback di sicurezza
        CURRENT_SNAPSHOT_ID = storage.get_active_snapshot_id(CURRENT_REPO_ID)
    
    if not CURRENT_SNAPSHOT_ID:
        print("❌ CRITICAL: Nessun snapshot attivo trovato.")
        sys.exit(1)
        
    print(f"🔑 Session Pinned to Snapshot: {CURRENT_SNAPSHOT_ID} (Repo: {CURRENT_REPO_ID})")

    # 3. EMBEDDING
    print("🤖 Verifica stato Embeddings...")
    try:
        for progress in indexer.embed(provider, batch_size=50, force_snapshot_id=CURRENT_SNAPSHOT_ID):
            if progress.get('status') == 'processing':
                print(f"\r⚡ Embedding: {progress.get('processed')} nodi...", end="", flush=True)
            elif progress.get('status') == 'skipped':
                print(f"\n⏩ {progress.get('message')}")
        print("\n✅ Embedding sincronizzato.")
    except Exception as e:
        print(f"⚠️ Errore non bloccante durante Embedding: {e}")

except Exception as e:
    print(f"⚠️ Errore critico inizializzazione: {e}")
    traceback.print_exc()
    sys.exit(1)

# Inizializzazione Facade
retriever = CodeRetriever(storage, provider)
reader = CodeReader(storage) 
navigator = CodeNavigator(storage)

print(f"✅ Sistema Inizializzato. Pronti.")

# ==============================================================================
# 2. DEFINIZIONE TOOLS E AGENT
# ==============================================================================

class SearchFiltersInput(BaseModel):
    path_prefix: Optional[Union[str, List[str]]] = Field(None, description="Filtra per cartella (es. 'src/core').")
    language: Optional[Union[str, List[str]]] = Field(None, description="Filtra per linguaggio (es. 'python').")
    role: Optional[Union[VALID_ROLES, List[VALID_ROLES]]] = Field(None, description="Include ruoli (es. 'entry_point').")
    category: Optional[Union[VALID_CATEGORIES, List[VALID_CATEGORIES]]] = Field(None, description="Include categorie.")

@tool
def search_codebase(query: str, filters: Optional[SearchFiltersInput] = None):
    """
    Cerca semanticamente nel codice.
    Usa SEMPRE questo tool per primo per trovare i file rilevanti.
    """
    filter_dict = filters.model_dump(exclude_none=True) if filters else None
    try:
        results = retriever.retrieve(
            query, 
            repo_id=CURRENT_REPO_ID, 
            snapshot_id=CURRENT_SNAPSHOT_ID, 
            limit=5, 
            strategy="hybrid", 
            filters=filter_dict
        )
        return "\n".join([r.render() for r in results]) if results else "Nessun risultato trovato."
    except Exception as e:
        return f"Errore ricerca: {e}"

@tool
def read_file_content(file_path: str, start_line: Optional[int] = None, end_line: Optional[int] = None):
    """
    Legge il contenuto di un file.
    Usa 'start_line' e 'end_line' per leggere solo le parti interessanti se il file è grande.
    """
    try:
        data = reader.read_file(CURRENT_SNAPSHOT_ID, file_path, start_line, end_line)
        return f"File: {data['file_path']}\nContent (L{data['start_line']}-{data['end_line']}):\n{data['content']}"
    except Exception as e:
        return f"Errore lettura: {e}"

@tool
def inspect_node_relationships(node_id: str):
    """
    Analizza le relazioni (Genitori, Figli, Chiamate) di un Chunk ID (UUID).
    Usa l'UUID trovato nell'output di search_codebase.
    """
    try:
        uuid.UUID(node_id)
    except ValueError:
        return f"ERRORE: '{node_id}' non è un UUID valido. Usa l'ID che trovi in 'NODE ID:' nei risultati di ricerca."

    report = []
    try:
        parent = navigator.read_parent_chunk(node_id)
        if parent: 
            report.append(f"⬆️ PARENT: {parent.get('type')} in {parent.get('file_path')}")
        else:
            report.append("⬆️ PARENT: None (Top-level node)")

        nxt = navigator.read_neighbor_chunk(node_id, "next")
        if nxt: 
            prev = nxt.get('content', '').split('\n')[0][:80]
            report.append(f"➡️ NEXT: {nxt.get('type')} (ID: {nxt.get('id')})\n   Preview: {prev}...")
            
        impact = navigator.analyze_impact(node_id)
        if impact:
            report.append(f"⬅️ CALLED BY ({len(impact)} refs):")
            for i in impact[:5]:
                report.append(f"   - {i['file']} L{i['line']} ({i['relation']})")
        else:
            report.append("⬅️ CALLED BY: None")
            
        return "\n".join(report)
    
    except Exception as e: 
        return f"Errore ispezione: {e}"
    
@tool
def find_folder(name_pattern: str):
    """Cerca il percorso di una cartella dato un nome parziale."""
    try:
        dirs = reader.find_directories(CURRENT_SNAPSHOT_ID, name_pattern)
        if not dirs:
            return f"Nessuna cartella trovata per '{name_pattern}'."
        return "Cartelle trovate:\n" + "\n".join([f"- {d}" for d in dirs])
    except Exception as e:
        return f"Errore ricerca cartelle: {e}"
    
@tool
def list_repo_structure(path: str = "", max_depth: int = 2):
    """Elenca file e cartelle nella repository (come 'ls')."""
    try:
        items = reader.list_directory(CURRENT_SNAPSHOT_ID, path)
        output = [f"Listing '{path or '/'}':"]
        for item in items:
            icon = "📁" if item['type'] == 'dir' else "📄"
            output.append(f"{icon} {item['name']}")
            
            # Anteprima per depth > 1
            if item['type'] == 'dir' and max_depth > 1:
                try:
                    sub_items = reader.list_directory(CURRENT_SNAPSHOT_ID, item['path'])
                    for i, sub in enumerate(sub_items):
                        if i >= 4: 
                            output.append(f"  └─ ... ({len(sub_items)-4} more)")
                            break
                        sub_icon = "  └─ 📁" if sub['type'] == 'dir' else "  └─ 📄"
                        output.append(f"{sub_icon} {sub['name']}")
                except: pass
        return "\n".join(output)
    except Exception as e: return f"Errore listing: {e}"


tools = [search_codebase, read_file_content, inspect_node_relationships, list_repo_structure, find_folder]

# Configurazione LLM
llm = ChatOpenAI(model="gpt-4o", temperature=0) 
SYSTEM_PROMPT = f"""
Sei un Senior Software Engineer esperto.
Stai lavorando sulla repository 'Flask' (Snapshot: {CURRENT_SNAPSHOT_ID[:8]}).

REGOLE:
1. Inizia esplorando la struttura con 'list_repo_structure' o cercando funzionalità con 'search_codebase'.
2. Leggi il codice con 'read_file_content' per confermare le tue ipotesi.
3. Se l'utente chiede dettagli architetturali, naviga il grafo con 'inspect_node_relationships'.
4. Rispondi in italiano tecnico.
"""

checkpointer = MemorySaver()
agent_executor = create_react_agent(llm, tools, checkpointer=checkpointer)

def chat_loop():
    print(f"\n🤖 AGENT READY (Snapshot: {CURRENT_SNAPSHOT_ID[:8]}). Type 'exit' to quit.")
    print("-" * 60)
    config = {"configurable": {"thread_id": f"session_{CURRENT_SNAPSHOT_ID}"}}
    
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.lower() in {"exit", "quit"}: break
        
        print("\nThinking...", end="", flush=True)
        try:
            events = agent_executor.stream(
                {"messages": [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_input)]}, 
                config=config, 
                stream_mode="values"
            )
            print("\r", end="")
            
            seen_ids = set()

            for event in events:
                if "messages" not in event: continue
                messages = event["messages"]
                if not messages: continue
                last_msg = messages[-1]
                
                msg_id = getattr(last_msg, "id", str(hash(str(last_msg))))
                if msg_id in seen_ids: continue
                seen_ids.add(msg_id)

                if last_msg.type == "ai" and getattr(last_msg, "tool_calls", None):
                    for tc in last_msg.tool_calls:
                        print(f"\n🛠️  CALLING: {tc.get('name')}")
                        print(f"    Args: {json.dumps(tc.get('args'), indent=2)}")

                elif last_msg.type == "tool":
                    print(f"\n📄 RESULT ({last_msg.name}):")
                    content = last_msg.content
                    preview = content[:500].replace("\n", "\n    ")
                    print(f"    {preview}")
                    if len(content) > 500: print("    ... (truncated)")

                elif last_msg.type == "ai" and last_msg.content:
                    if not getattr(last_msg, "tool_calls", None):
                        print(f"\n🤖 AGENT:\n{last_msg.content}\n")

        except Exception as e:
            print(f"\n❌ Errore agente: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    chat_loop()