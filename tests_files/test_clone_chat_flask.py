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
# Add the src directory to the python path so we can import the package
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

# Import core components
from code_graph_indexer import CodebaseIndexer, CodeRetriever, CodeReader, CodeNavigator
from code_graph_indexer.storage.postgres import PostgresGraphStorage
from code_graph_indexer.schema import VALID_ROLES, VALID_CATEGORIES

# Import embedding provider (fallback if file name differs)
try:
    from code_graph_indexer.providers.openai_emb import OpenAIEmbeddingProvider
except ImportError:
    from code_graph_indexer.providers.embedding import OpenAIEmbeddingProvider

# ==============================================================================
# 1. SETUP SYSTEM (Clone Flask + Postgres)
# ==============================================================================

# Database configuration
DB_PORT = "5433" 
DB_URL = f"postgresql://sheep_user:sheep_password@localhost:{DB_PORT}/sheep_index"

# --- CLONE FLASK ---
REPO_URL = "https://github.com/pallets/flask.git"
REPO_PATH = tempfile.mkdtemp(prefix="sheep_agent_flask_")

def cleanup_temp_dir():
    if os.path.exists(REPO_PATH):
        print(f"\n🧹 Cleaning up temporary directory: {REPO_PATH}")
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
    print("✅ Clone completed.")
except subprocess.CalledProcessError as e:
    print(f"❌ Error during clone: {e}")
    sys.exit(1)

print(f"🐘 Connecting to: {DB_URL}")

try:
    # Initialize storage and embedding provider
    storage = PostgresGraphStorage(DB_URL, vector_dim=1536)
    # Ensure OPENAI_API_KEY is set in your environment
    provider = OpenAIEmbeddingProvider(model="text-embedding-3-small")
except Exception as e:
    print(f"❌ Infrastructure Setup Error: {e}")
    sys.exit(1)

# --- GLOBAL SESSION VARIABLES ---
CURRENT_REPO_ID = None
CURRENT_SNAPSHOT_ID = None

# --- INDEXING & EMBEDDING LOGIC ---
try:
    indexer = CodebaseIndexer(REPO_PATH, storage)
    
    # Get basic repo info for logging
    repo_meta = indexer.parser.metadata_provider.get_repo_info()
    print(f"📂 Repository Context: {repo_meta['url']} (Branch: {repo_meta['branch']})")

    # 1. INDEXING (Blue-Green Deployment)
    # The indexer handles idempotency and snapshot creation.
    print("\n🚀 Starting Indexing (Snapshot Check)...")
    try:
        # index() returns the active snapshot ID. 
        # Set force=True to re-index even if a snapshot exists.
        CURRENT_SNAPSHOT_ID = indexer.index(force=False) 
        print(f"✅ Indexing completed. Snapshot ID: {CURRENT_SNAPSHOT_ID}")
    except Exception as e:
        print(f"❌ Error during Indexing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # 2. SESSION PINNING
    # We resolve the stable Repo ID for logging, but the Snapshot ID is key.
    CURRENT_REPO_ID = storage.ensure_repository(repo_meta['url'], repo_meta['branch'], repo_meta['name'])
    
    if not CURRENT_SNAPSHOT_ID:
        # Fallback check (should be covered by indexer return)
        CURRENT_SNAPSHOT_ID = storage.get_active_snapshot_id(CURRENT_REPO_ID)
    
    if not CURRENT_SNAPSHOT_ID:
        print("❌ CRITICAL: Indexing finished but no active snapshot found.")
        sys.exit(1)
        
    print(f"🔑 Session Pinned to Snapshot: {CURRENT_SNAPSHOT_ID} (Repo: {CURRENT_REPO_ID})")

    # 3. EMBEDDING (Vectors)
    # Explicitly pass the pinned snapshot ID to ensure we embed the correct version.
    print("🤖 Verifying Embeddings status...")
    try:
        count = 0
        # Embedder works in streaming mode
        for progress in indexer.embed(provider, batch_size=50, force_snapshot_id=CURRENT_SNAPSHOT_ID):
            status = progress.get('status')
            if status == 'processing':
                print(f"\r⚡ Embedding: {progress.get('processed')} nodes...", end="", flush=True)
            elif status == 'skipped':
                print(f"\n⏩ {progress.get('message')}")
        print("\n✅ Embedding synchronized.")
    except Exception as e:
        print(f"⚠️ Non-blocking error during Embedding: {e}")

except Exception as e:
    print(f"⚠️ Critical Initialization Error: {e}")
    sys.exit(1)

# Initialize Facades
retriever = CodeRetriever(storage, provider)
reader = CodeReader(storage) 
navigator = CodeNavigator(storage)

print(f"✅ System Initialized. Ready for questions about Flask.")

# ==============================================================================
# 2. TOOLS AND AGENT DEFINITION
# ==============================================================================

class SearchFiltersInput(BaseModel):
    path_prefix: Optional[Union[str, List[str]]] = Field(None, description="Filter by folder path.")
    language: Optional[Union[str, List[str]]] = Field(None, description="Filter by programming language.")
    role: Optional[Union[VALID_ROLES, List[VALID_ROLES]]] = Field(None, description="Include specific roles.")
    exclude_role: Optional[Union[VALID_ROLES, List[VALID_ROLES]]] = Field(None, description="Exclude roles.")
    category: Optional[Union[VALID_CATEGORIES, List[VALID_CATEGORIES]]] = Field(None, description="Include categories.")

@tool
def search_codebase(query: str, filters: Optional[SearchFiltersInput] = None):
    """
    Search the codebase semantically.
    ALWAYS use this tool first to find relevant code functionality.
    """
    filter_dict = filters.model_dump(exclude_none=True) if filters else None
    try:
        # [NEW] Pass snapshot_id to ensure searching the pinned version
        results = retriever.retrieve(
            query, 
            repo_id=CURRENT_REPO_ID, 
            snapshot_id=CURRENT_SNAPSHOT_ID, 
            limit=5, 
            strategy="hybrid", 
            filters=filter_dict
        )
        return "\n".join([r.render() for r in results]) if results else "No results found."
    except Exception as e:
        return f"Search Error: {e}"

@tool
def read_file_content(file_path: str, start_line: Optional[int] = None, end_line: Optional[int] = None):
    """
    Read the content of a specific file.
    """
    try:
        # Use SNAPSHOT ID to read the file version consistent with the index
        data = reader.read_file(CURRENT_SNAPSHOT_ID, file_path, start_line, end_line)
        return f"File: {data['file_path']}\nContent:\n{data['content']}"
    except Exception as e:
        return f"Read Error: {e}"

@tool
def inspect_node_relationships(node_id: str):
    """
    Analyze relationships (Parent, Next, Callers, Calls) for a given Chunk ID (UUID).
    Use the UUID found in search_codebase results.
    """
    try:
        uuid.UUID(node_id)
    except ValueError:
        return f"ERROR: '{node_id}' is not a valid UUID. Use the ID found via search_codebase."

    report = []
    try:
        # Navigator works on node IDs which are unique per snapshot
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
        return f"Inspection Error: {e}"
    
@tool
def find_folder(name_pattern: str):
    """Find a folder path given a partial name."""
    try:
        # Use snapshot_id for consistent directory structure
        dirs = reader.find_directories(CURRENT_SNAPSHOT_ID, name_pattern)
        if not dirs:
            return f"No folders found containing '{name_pattern}'."
        return "Folders found:\n" + "\n".join([f"- {d}" for d in dirs])
    except Exception as e:
        return f"Folder Search Error: {e}"
    
@tool
def list_repo_structure(path: str = "", max_depth: int = 2):
    """List files and folders in the repository."""
    try:
        # Use snapshot_id to list the structure of THIS version
        items = reader.list_directory(CURRENT_SNAPSHOT_ID, path)
        output = [f"Listing '{path or '/'}':"]
        for item in items:
            icon = "📁" if item['type'] == 'dir' else "📄"
            output.append(f"{icon} {item['name']}")
            if item['type'] == 'dir' and max_depth > 1:
                try:
                    sub_items = reader.list_directory(CURRENT_SNAPSHOT_ID, item['path'])
                    for i, sub in enumerate(sub_items):
                        if i >= 5: 
                            output.append(f"  └─ ... ({len(sub_items)-5} more)")
                            break
                        sub_icon = "  └─ 📁" if sub['type'] == 'dir' else "  └─ 📄"
                        output.append(f"{sub_icon} {sub['name']}")
                except: pass
        return "\n".join(output)
    except Exception as e: return f"Listing Error: {e}"


tools = [search_codebase, read_file_content, inspect_node_relationships, list_repo_structure, find_folder]

# Initialize LangChain Agent
llm = ChatOpenAI(model="gpt-4o", temperature=0) # GPT-4o recommended for complex RAG tasks
SYSTEM_PROMPT = f"""
You are an expert Senior Software Engineer.
You are working on the 'Flask' repository (Snapshot: {CURRENT_SNAPSHOT_ID[:8]}).

RULES:
1. Use 'search_codebase' to find relevant code.
2. Use 'read_file_content' to read full implementation if needed.
3. If the user asks for architectural explanations, use 'inspect_node_relationships' on key nodes found.
4. Do not invent code. Base your answers on the read files.
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
            # Usiamo lo stream per intercettare ogni passo
            events = agent_executor.stream(
                {"messages": [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_input)]}, 
                config=config, 
                stream_mode="values"
            )
            print("\r", end="")
            
            # Teniamo traccia dei messaggi già visti per non ristamparli (stream "values" accumula)
            seen_ids = set()

            for event in events:
                if "messages" not in event: continue
                messages = event["messages"]
                
                # Analizziamo solo l'ultimo messaggio della catena
                if not messages: continue
                last_msg = messages[-1]
                
                # Se abbiamo già stampato questo messaggio, saltiamo
                # (Usiamo ID o content hash come fallback)
                msg_id = getattr(last_msg, "id", str(hash(str(last_msg))))
                if msg_id in seen_ids: continue
                seen_ids.add(msg_id)

                # --- 1. L'Agente sta decidendo di chiamare un Tool ---
                if last_msg.type == "ai" and getattr(last_msg, "tool_calls", None):
                    for tc in last_msg.tool_calls:
                        print(f"\n🛠️  CALLING: {tc.get('name')}")
                        print(f"    Args: {json.dumps(tc.get('args'), indent=2)}")

                # --- 2. Il Tool ha risposto (QUESTO È QUELLO CHE TI SERVE) ---
                elif last_msg.type == "tool":
                    print(f"\n📄 RESULT ({last_msg.name}):")
                    content = last_msg.content
                    # Limitiamo l'output a 500 caratteri per non intasare il terminale, ma sufficienti per capire l'errore
                    preview = content[:500].replace("\n", "\n    ")
                    print(f"    {preview}")
                    if len(content) > 500:
                        print("    ... (truncated)")

                # --- 3. Risposta finale dell'Agente ---
                elif last_msg.type == "ai" and last_msg.content:
                    # A volte l'AI manda content + tool_calls insieme. Stampiamo content solo se è la risposta finale.
                    if not getattr(last_msg, "tool_calls", None):
                        print(f"\n🤖 AGENT:\n{last_msg.content}\n")

        except Exception as e:
            print(f"\n❌ Errore agente: {e}")
            import traceback
            traceback.print_exc()
if __name__ == "__main__":
    chat_loop()