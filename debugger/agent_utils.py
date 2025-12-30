import logging
import uuid
import json
from typing import Optional, List, Union, Dict, Any
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

from src.code_graph_indexer.retriever import CodeRetriever
from src.code_graph_indexer.reader import CodeReader
from src.code_graph_indexer.navigator import CodeNavigator
from src.code_graph_indexer.schema import VALID_ROLES, VALID_CATEGORIES
from debugger.database import get_storage
from src.code_graph_indexer.providers.embedding import OpenAIEmbeddingProvider

logger = logging.getLogger(__name__)

# --- Tool Input Schemas ---

class SearchFiltersInput(BaseModel):
    path_prefix: Optional[Union[str, List[str]]] = Field(None, description="Filtra per cartella.")
    language: Optional[Union[str, List[str]]] = Field(None, description="Filtra per linguaggio.")
    role: Optional[Union[VALID_ROLES, List[VALID_ROLES]]] = Field(None, description="Include ruoli specifici (SOLO se esplicitamente richiesto/noto).")
    exclude_role: Optional[Union[VALID_ROLES, List[VALID_ROLES]]] = Field(None, description="Esclude ruoli.")
    category: Optional[Union[VALID_CATEGORIES, List[VALID_CATEGORIES]]] = Field(None, description="Include categorie.")
    exclude_category: Optional[Union[VALID_CATEGORIES, List[VALID_CATEGORIES]]] = Field(None, description="Esclude categorie.")

# --- RepoAgent Class ---

class RepoAgent:
    def __init__(self, repo_id: str):
        self.repo_id = repo_id
        self.storage = get_storage()
        # Initialize providers - assuming OpenAI is configured via env vars
        self.provider = OpenAIEmbeddingProvider(model="text-embedding-3-small")
        
        # We initialize facade components but tools will use them
        self.retriever = CodeRetriever(self.storage, self.provider)
        self.reader = CodeReader(self.storage)
        self.navigator = CodeNavigator(self.storage)
        
        self.tools = self._create_tools()
        self.llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        
        self.system_prompt = f"""
        Sei un Senior Software Engineer che analizza la repository.
        Hai accesso a un Knowledge Graph avanzato.
        
        REPO ID: {self.repo_id}
        
        Usa 'search_codebase' per trovare punti di ingresso.
        Usa 'read_file' per leggere il codice.
        Usa 'inspect_node' sugli UUID trovati per capire le dipendenze (Graph RAG).
        
        IMPORTANTE: 
        - Usa `search_codebase` SENZA filtri inizialmente, a meno che l'utente non specifichi "classe", "funzione", ecc.
        - Se la ricerca non trova nulla, riprova con query più generiche.
        
        Rispondi in modo conciso e tecnico.
        """
        
        # In-memory checkpointer for this session
        self.checkpointer = MemorySaver()
        self.agent_executor = create_react_agent(self.llm, self.tools, checkpointer=self.checkpointer)

    @property
    def active_snapshot_id(self):
        snap = self.storage.get_active_snapshot_id(self.repo_id)
        if not snap:
            raise ValueError(f"Nessuno snapshot attivo per repo {self.repo_id}")
        return snap

    def _create_tools(self):
        # Closure to access self
        
        @tool
        def search_codebase(query: str, filters: Optional[SearchFiltersInput] = None):
            """
            Cerca semanticamente nel codice (Retrieval Augmented Generation).
            Usa questo per trovare 'dove' si trovano le funzionalità.
            EVITA filtri se non sei sicuro (es. non mettere role='class' se cerchi testo generico).
            """
            f_dict = filters.model_dump(exclude_none=True) if filters else None
            logger.info(f"🔎 Agent Search: query='{query}' filters={f_dict}")
            try:
                # Use active snapshot implicitly via retriever or pass it
                snap_id = self.active_snapshot_id
                results = self.retriever.retrieve(
                    query, 
                    repo_id=self.repo_id, 
                    snapshot_id=snap_id,
                    limit=5, 
                    strategy="hybrid", 
                    filters=f_dict
                )
                
                # FALLBACK LOGIC: If filters were used and no results found, try without filters
                if not results and f_dict:
                    logger.info(f"🔎 Agent Search: No results with filters {f_dict}. Retrying raw...")
                    results = self.retriever.retrieve(
                        query, 
                        repo_id=self.repo_id, 
                        snapshot_id=snap_id,
                        limit=5, 
                        strategy="hybrid", 
                        filters=None
                    )
                    if results:
                        return f"⚠️ Nessun risultato con i filtri {f_dict}, ma ho trovato questi risultati generali:\n" + "\n".join([r.render() for r in results])

                if not results:
                    logger.info("🔎 Agent Search: No results found.")
                    return "Nessun risultato trovato."
                
                logger.info(f"🔎 Agent Search: Found {len(results)} results.")
                return "\n".join([r.render() for r in results])
            except Exception as e:
                logger.error(f"🔎 Agent Search Error: {e}")
                return f"Errore ricerca: {e}"

        @tool
        def read_file(file_path: str, start_line: Optional[int] = None, end_line: Optional[int] = None):
            """Legge il contenuto di un file."""
            try:
                snap_id = self.active_snapshot_id
                data = self.reader.read_file(snap_id, file_path, start_line, end_line)
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
                refs = self.navigator.analyze_impact(node_id)
                if refs:
                    report.append(f"⬅️ CALLED BY ({len(refs)}):")
                    for r in refs[:5]: report.append(f"   - {r['file']} L{r['line']} ({r['relation']})")
                else:
                    report.append("⬅️ CALLED BY: None detected.")
                
                # 2. Contesto (Dove sono?)
                parent = self.navigator.read_parent_chunk(node_id)
                if parent:
                    report.append(f"⬆️ PARENT: {parent.get('type')} in {parent.get('file_path')}")

                return "\n".join(report)
            except Exception as e:
                return f"Errore ispezione: {e}"

        return [search_codebase, read_file, inspect_node]

    async def stream_chat(self, message: str, thread_id: str):
        config = {"configurable": {"thread_id": thread_id}}
        inputs = {"messages": [SystemMessage(content=self.system_prompt), HumanMessage(content=message)]}
        
        try:
            # Unlike the test which prints, here we yield ndjson events for the frontend
            async for event in self.agent_executor.astream(inputs, config=config):
                
                # 'agent' event contains the AI message (thought or final answer)
                if 'agent' in event:
                    msg = event['agent']['messages'][-1]
                    if msg.content:
                        yield json.dumps({
                            "type": "message",
                            "content": msg.content
                        }) + "\n"
                    
                    if getattr(msg, 'tool_calls', None):
                        for tc in msg.tool_calls:
                            yield json.dumps({
                                "type": "tool_call",
                                "name": tc.get('name'),
                                "args": tc.get('args'),
                                "id": tc.get('id')
                            }) + "\n"

                # 'tools' event contains the output of tool execution
                if 'tools' in event:
                    msg = event['tools']['messages'][-1]
                    yield json.dumps({
                        "type": "tool_output",
                        "name": msg.name,
                        "content": msg.content,
                        "tool_call_id": msg.tool_call_id
                    }) + "\n"

        except Exception as e:
            logger.error(f"Agent error: {e}")
            yield json.dumps({"type": "error", "content": str(e)}) + "\n"

# Global cache for agents to persist memory across requests (simple version)
# In a real app, we'd persist checkpoints to DB.
_agents: Dict[str, RepoAgent] = {}

def get_agent(repo_id: str) -> RepoAgent:
    if repo_id not in _agents:
        _agents[repo_id] = RepoAgent(repo_id)
    return _agents[repo_id]
