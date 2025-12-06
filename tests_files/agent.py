
import os
import sys
import json
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

# LangGraph
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

# --- IMPORT LIBRERIA ---
from code_graph_indexer import CodebaseIndexer, CodeRetriever, CodeReader, CodeNavigator
from code_graph_indexer.storage.postgres import PostgresGraphStorage
from code_graph_indexer.schema import VALID_ROLES, VALID_CATEGORIES

# Import dinamico provider
try:
    from code_graph_indexer.providers.openai_emb import OpenAIEmbeddingProvider
except ImportError:
    from code_graph_indexer.providers.embedding import OpenAIEmbeddingProvider

# ==============================================================================
# 1. SETUP SISTEMA (Enterprise: Postgres + OpenAI)
# ==============================================================================

# Configura qui la porta corretta (5433 o 5435 a seconda del tuo docker ps)
DB_PORT = "5433" 
DB_URL = f"postgresql://sheep_user:sheep_password@localhost:{DB_PORT}/sheep_index"
REPO_PATH = "/Users/filippodaminato/Desktop/test_repos/7f10a3a2e3b9/worktrees/main"

print(f"🐘 Connecting to: {DB_URL}")

try:
    # [POSTGRES] Usiamo Postgres con vettori OpenAI (1536)
    storage = PostgresGraphStorage(DB_URL, vector_dim=1536)
    provider = OpenAIEmbeddingProvider(model="text-embedding-3-small")
except Exception as e:
    print(f"❌ Errore Setup Infrastruttura: {e}")
    sys.exit(1)

# --- LOGICA COLD START (Auto-Indexing) ---
try:
    indexer = CodebaseIndexer(REPO_PATH, storage)
    repo_info = indexer.parser.metadata_provider.get_repo_info()
    url = repo_info['url']
    branch = repo_info['branch']
    print(f"📂 Repository: {url} (Branch: {branch})")

    existing_record = storage.get_repository_by_context(url, branch)

    if not existing_record or existing_record.get('status') != 'completed':
        print("\n🚀 REPO NON TROVATA O INCOMPLETA. AVVIO INDEXING AUTOMATICO...")
        try:
            indexer.index(force=True)
            print("🤖 Generazione Embeddings in corso...")
            list(indexer.embed(provider, batch_size=100))
            print("✅ Setup completato! DB popolato.")
            existing_record = storage.get_repository_by_context(url, branch)
        except Exception as e:
            print(f"❌ Errore durante Auto-Indexing: {e}")
            sys.exit(1)
    else:
        print("✅ Repo già indicizzata nel DB. Skipping setup.")

    CURRENT_REPO_ID = existing_record['id']
    print(f"🔑 Session Repo ID: {CURRENT_REPO_ID}")

except Exception as e:
    print(f"⚠️ Errore critico recupero repo: {e}")
    sys.exit(1)

# Facade
retriever = CodeRetriever(storage, provider)
reader = CodeReader(storage)
navigator = CodeNavigator(storage)


print(f"✅ Sistema Inizializzato. Repo ID: {CURRENT_REPO_ID}")

# ==============================================================================
# 2. DEFINIZIONE INPUT TOOL
# ==============================================================================


class SearchFiltersInput(BaseModel):
    """Filtri opzionali per raffinare la ricerca."""

    path_prefix: Optional[Union[str, List[str]]] = Field(
        None, description="Filtra per cartella. Es: 'src/auth/'"
    )
    language: Optional[Union[str, List[str]]] = Field(
        None, description="Filtra per linguaggio/estensione. Es: 'python'"
    )
    role: Optional[Union[VALID_ROLES, List[VALID_ROLES]]] = Field(
        None, description="Include SOLO questi ruoli (es. entry_point)."
    )
    exclude_role: Optional[Union[VALID_ROLES, List[VALID_ROLES]]] = Field(
        None, description="Esclude ruoli (es. test_case)."
    )
    category: Optional[Union[VALID_CATEGORIES, List[VALID_CATEGORIES]]] = Field(
        None, description="Include categorie."
    )
    exclude_category: Optional[Union[VALID_CATEGORIES, List[VALID_CATEGORIES]]] = Field(
        None, description="Esclude categorie."
    )


# ==============================================================================
# 3. DEFINIZIONE TOOLS
# ==============================================================================
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

@tool
def search_codebase(query: str, filters: Optional[SearchFiltersInput] = None):
    """
    Cerca semanticamente nel codice. Usa questo PRIMA di tutto.
    Usa i filtri per ridurre il rumore (es. exclude_category='test').
    """
    filter_dict = filters.model_dump(exclude_none=True) if filters else None
    results = retriever.retrieve(
        query,
        repo_id=CURRENT_REPO_ID,
        limit=5,
        strategy="hybrid",
        filters=filter_dict,
    )
    if not results:
        return "Nessun risultato trovato."

    return "\n".join([r.render() for r in results])


@tool
def read_file_content(
    file_path: str, start_line: Optional[int] = None, end_line: Optional[int] = None
):
    """Legge il contenuto fisico di un file."""
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
    """
    report: List[str] = []

    # 1. Parent
    parent = navigator.read_parent_chunk(node_id)
    if parent:
        report.append(f"⬆️ PARENT: {parent.get('type')} in {parent.get('file_path')}")

    # 2. Next
    nxt = navigator.read_neighbor_chunk(node_id, "next")
    if nxt:
        preview = nxt.get("content", "").split("\n")[0][:80]
        report.append(
            f"➡️ NEXT: {nxt.get('type')} (ID: {nxt.get('id')})\n   Preview: {preview}..."
        )

    # 3. Impact (chi chiama questo chunk)
    impact = navigator.analyze_impact(node_id)
    if impact:
        report.append(f"⬅️ CALLED BY ({len(impact)} refs):")
        for i in impact[:5]:
            report.append(f"   - {i['file']} L{i['line']} ({i['relation']})")

    # 4. Pipeline (cosa viene chiamato da questo chunk)
    pipe = navigator.visualize_pipeline(node_id)
    if pipe and pipe.get("call_graph"):
        report.append(f"⤵️ CALLS: {json.dumps(pipe['call_graph'], indent=2)}")

    if not report:
        return "Nessuna relazione trovata per questo node_id."

    return "\n".join(report)


tools = [search_codebase, read_file_content, inspect_node_relationships,list_repo_structure]

# ==============================================================================
# 4. AGENTE LANGGRAPH
# ==============================================================================

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

SYSTEM_PROMPT = (
    "Sei un Senior Software Engineer esperto.\n"
    "Rispondi alle domande sul codice usando SOLO i tool a disposizione.\n"
    "NON inventare codice o file che non esistono.\n"
    "Usa sempre `search_codebase` come primo passo quando non sei sicuro."
)

checkpointer = MemorySaver()

# create_react_agent (versioni recenti) -> niente messages_modifier/state_modifier,
# passiamo il system prompt direttamente dentro ai messages ad ogni invocazione.
agent_executor = create_react_agent(
    llm,
    tools,
    checkpointer=checkpointer,
)

# ==============================================================================
# 5. LOOP INTERATTIVO
# ==============================================================================


def chat_loop():
    print("\n🤖 AGENT READY (LangGraph). Type 'exit' to quit.")
    print("-" * 60)

    # ID univoco per questa sessione (per la memoria LangGraph)
    config = {"configurable": {"thread_id": "session_1"}}

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in {"exit", "quit"}:
            break

        print("\nThinking...", end="", flush=True)

        # Streaming dello stato del grafo
        events = agent_executor.stream(
            {
                "messages": [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=user_input),
                ]
            },
            config=config,
            stream_mode="values",
        )

        # Cancella la scritta "Thinking..."
        print("\r", end="")

        for event in events:
            if "messages" not in event:
                continue

            last_msg = event["messages"][-1]

            # Messaggi AI
            if getattr(last_msg, "type", None) == "ai":
                tool_calls = getattr(last_msg, "tool_calls", None)

                # Se sta chiamando tool, logghiamo quali
                if tool_calls:
                    for tc in tool_calls:
                        name = tc.get("name")
                        args = tc.get("args")
                        print(f"🛠️  Calling: {name} ({args})")
                else:
                    # Risposta finale all'utente
                    print(f"🤖 Agent:\n{last_msg.content}\n")


if __name__ == "__main__":
    chat_loop()
