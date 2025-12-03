import os
os.environ["TOKENIZERS_PARALLELISM"] = "false"
import sys
import json
from typing import Optional, List, Union

# --- CONFIGURAZIONE PATH -------------------------------------------------------
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, "..", "src"))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

# --- ENV / DOTENV --------------------------------------------------------------
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    print("⚠️ python-dotenv non trovato. Assicurati di averlo installato.")

from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage

# LangGraph
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

# --- IMPORT LIBRERIA CODE GRAPH INDEXER ----------------------------------------
from code_graph_indexer import (
    CodebaseIndexer,
    CodeRetriever,
    CodeReader,
    CodeNavigator,
)
from code_graph_indexer.storage.sqlite import SqliteGraphStorage
from code_graph_indexer.providers.embedding import FastEmbedProvider
from code_graph_indexer.schema import VALID_ROLES, VALID_CATEGORIES

# ==============================================================================
# 1. SETUP SISTEMA (Backend)
# ==============================================================================

# Percorsi (verifica che siano corretti per il tuo ambiente)
REPO_PATH = "/Users/filippodaminato/Desktop/test_repos/7f10a3a2e3b9/worktrees/main"
DB_PATH = "sheep_index_test.db"

if not os.path.exists(DB_PATH):
    print(f"⚠️ ATTENZIONE: DB {DB_PATH} non trovato. Esegui prima il notebook di indexing!")
    storage = SqliteGraphStorage(":memory:")  # Fallback in-memory
else:
    storage = SqliteGraphStorage(DB_PATH)

provider = FastEmbedProvider()

# Indexer solo per recuperare ID repo
indexer = CodebaseIndexer(REPO_PATH, storage)
CURRENT_REPO_ID = "3f5aaca6-e8e5-4f3c-95a6-3cabc68c0eee"	

# Motori
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


tools = [search_codebase, read_file_content, inspect_node_relationships]

# ==============================================================================
# 4. AGENTE LANGGRAPH
# ==============================================================================

llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)

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
