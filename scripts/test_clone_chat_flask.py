import os
import sys
import asyncio
import shutil
import tempfile
import subprocess
import traceback
import json
import logging
from typing import Optional, List, Union

# --- ENV & PATH SETUP ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, "..", "src"))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

# --- LIBRARIES ---
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

# --- SHEEP COMPONENTS ---
from crader.indexer import CodebaseIndexer
from crader.providers.embedding import OpenAIEmbeddingProvider
from crader.storage.connector import PooledConnector
from crader.retriever import CodeRetriever
from crader.reader import CodeReader
from crader.navigator import CodeNavigator
from crader.schema import VALID_ROLES, VALID_CATEGORIES

# --- CONFIGURAZIONE ---
DB_URL = os.getenv("DB_URL", "postgresql://sheep_user:sheep_password@localhost:6432/sheep_index")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
REPO_URL = "https://github.com/pallets/flask.git"
REPO_BRANCH = "main"

# Logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("AGENT_TEST")
# Riduciamo il rumore delle librerie esterne
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("crader").setLevel(logging.INFO)

# ==============================================================================
# 1. INFRASTRUCTURE SETUP
# ==============================================================================

def setup_repo(base_dir: str) -> str:
    """Clona Flask in una cartella temporanea."""
    repo_path = os.path.join(base_dir, "flask_repo")
    if os.path.exists(repo_path):
        shutil.rmtree(repo_path)
    
    logger.info(f"🔄 Cloning Flask ({REPO_URL}) into {repo_path}...")
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", REPO_BRANCH, REPO_URL, repo_path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return f"file://{repo_path}"

async def main_async():
    if not OPENAI_KEY:
        logger.error("❌ OPENAI_API_KEY mancante. Impossibile avviare l'agente.")
        return

    tmp_dir = tempfile.mkdtemp()
    indexer_instance = None
    
    try:
        # 1. CLONE
        repo_url_local = setup_repo(tmp_dir)
        
        # 2. INIT COMPONENTS
        logger.info(f"🔌 Connecting to DB: {DB_URL}")
        
        # Indexer (gestisce lo storage internamente con PooledConnector)
        indexer = CodebaseIndexer(repo_url_local, REPO_BRANCH, db_url=DB_URL)
        indexer_instance = indexer # Reference for cleanup
        
        # Provider (Enterprise Async)
        provider = OpenAIEmbeddingProvider(model="text-embedding-3-small", max_concurrency=10)
        
        # 3. RUN INDEXING PIPELINE (Parsing -> SCIP -> Graph)
        logger.info("🚀 Phase 1: Parsing & Graph Building...")
        # force=False: se abbiamo già indicizzato questo commit, riusa lo snapshot
        snapshot_id = indexer.index(force=False)
        
        if snapshot_id == "queued":
            logger.warning("⏸️  Repo is currently locked/indexing by another process.")
            return

        logger.info(f"✅ Snapshot Active: {snapshot_id}")

        # 4. RUN EMBEDDING PIPELINE (Async Staging -> OpenAI)
        logger.info("💸 Phase 2: Embedding Check (Async Pipeline)...")
        
        # Questo consumerà il generatore asincrono
        async for update in indexer.embed(provider, batch_size=200, mock_api=False):
            status = update['status']
            if status == 'embedding_progress':
                print(f"   ✨ Embedding: {update.get('total_embedded')} vectors...", end='\r')
            elif status == 'completed':
                stats = update
                print(f"\n✅ Embedding Sync Complete. (New: {stats.get('newly_embedded')}, Recovered: {stats.get('recovered_from_history')})")

        # 5. SETUP RETRIEVAL FACADE
        # Usiamo il connettore dell'indexer per risparmiare risorse, o ne creiamo uno nuovo
        retriever = CodeRetriever(indexer.storage, provider)
        reader = CodeReader(indexer.storage)
        navigator = CodeNavigator(indexer.storage)
        
        # Otteniamo repo_id stabile per le query
        repo_info = indexer.storage.get_repository(indexer.storage.ensure_repository(repo_url_local, REPO_BRANCH, "flask"))
        repo_id = repo_info['id']

        # ==============================================================================
        # 6. AGENT TOOLS DEFINITION
        # ==============================================================================
        
        # Definiamo i tool dentro il main per accedere alle istanze (closure)
        # In un'app reale, useremmo dependency injection o una classe container.

        class SearchFiltersInput(BaseModel):
            path_prefix: Optional[str] = Field(None, description="Filtra per cartella (es. 'src/').")
            language: Optional[str] = Field(None, description="Filtra per linguaggio (es. 'python').")

        @tool
        def search_codebase(query: str, filters: Optional[SearchFiltersInput] = None):
            """
            Cerca semanticamente nel codice (Retrieval Augmented Generation).
            Usa questo per trovare 'dove' si trovano le funzionalità.
            """
            f_dict = filters.model_dump(exclude_none=True) if filters else None
            try:
                results = retriever.retrieve(
                    query, 
                    repo_id=repo_id, 
                    snapshot_id=snapshot_id, 
                    limit=5, 
                    strategy="hybrid",
                    filters=f_dict
                )
                if not results: return "Nessun risultato trovato."
                return "\n".join([r.render() for r in results])
            except Exception as e:
                return f"Errore ricerca: {e}"

        @tool
        def read_file(file_path: str, start_line: Optional[int] = None, end_line: Optional[int] = None):
            """Legge il contenuto di un file."""
            try:
                data = reader.read_file(snapshot_id, file_path, start_line, end_line)
                if not data: return "File non trovato o vuoto."
                return f"File: {data['file_path']}\nContent:\n{data['content']}"
            except Exception as e:
                return f"Errore lettura: {e}"

        @tool
        def inspect_node(node_id: str):
            """
            Esamina le relazioni di un nodo specifico (UUID).
            Fornisce: Parent (File/Class), Next Sibling, e Callers (chi lo usa).
            """
            try:
                report = []
                # 1. Impatto (Chi mi chiama?)
                refs = navigator.analyze_impact(node_id)
                if refs:
                    report.append(f"⬅️ CALLED BY ({len(refs)}):")
                    for r in refs[:5]: report.append(f"   - {r['file']} L{r['line']} ({r['relation']})")
                else:
                    report.append("⬅️ CALLED BY: None detected.")
                
                # 2. Contesto (Dove sono?)
                parent = navigator.read_parent_chunk(node_id)
                if parent:
                    report.append(f"⬆️ PARENT: {parent.get('type')} in {parent.get('file_path')}")

                return "\n".join(report)
            except Exception as e:
                return f"Errore ispezione: {e}"

        tools = [search_codebase, read_file, inspect_node]

        # ==============================================================================
        # 7. CHAT LOOP
        # ==============================================================================
        
        llm = ChatOpenAI(model="gpt-4o", temperature=0)
        
        SYSTEM_PROMPT = f"""
        Sei un Senior Software Engineer che analizza la repository 'Flask'.
        Hai accesso a un Knowledge Graph avanzato.
        
        SNAPSHOT ID: {snapshot_id}
        
        Usa 'search_codebase' per trovare punti di ingresso.
        Usa 'read_file' per leggere il codice.
        Usa 'inspect_node' sugli UUID trovati per capire le dipendenze (Graph RAG).
        
        Rispondi in modo conciso e tecnico.
        """

        checkpointer = MemorySaver()
        agent_executor = create_react_agent(llm, tools, checkpointer=checkpointer)
        
        config = {"configurable": {"thread_id": "test_session_1"}}

        print("\n" + "="*60)
        print(f"🤖 AGENT READY ON FLASK REPO")
        print("="*60)

        while True:
            try:
                user_input = input("\nUser: ").strip()
                if user_input.lower() in ["exit", "quit"]: break
                
                print("Thinking...", end="", flush=True)
                
                # Stream degli eventi dell'agente
                # Nota: create_react_agent è sync o async? LangGraph supporta entrambi.
                # Qui usiamo ainvoke (async invoke)
                
                async for event in agent_executor.astream(
                    {"messages": [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_input)]},
                    config=config
                ):
                    # Parsing semplificato dell'output streaming
                    if 'agent' in event:
                        msg = event['agent']['messages'][-1]
                        if msg.content:
                            print(f"\r🤖 Agent: {msg.content}")
                        if msg.tool_calls:
                            for tc in msg.tool_calls:
                                print(f"\n🛠️  Call: {tc['name']} {tc['args']}")
                    
                    if 'tools' in event:
                        msg = event['tools']['messages'][-1]
                        print(f"📄 Result: {msg.content[:200]}...")

            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"❌ Error: {e}")
                traceback.print_exc()

    except Exception as e:
        logger.error(f"❌ Critical Error: {e}", exc_info=True)
    finally:
        logger.info("🧹 Cleanup...")
        if indexer_instance:
            indexer_instance.close()
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main_async())