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
    role: Optional[Union[VALID_ROLES, List[VALID_ROLES]]] = Field(None, description="Include ruoli specifici.")
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
        
        self.retriever = CodeRetriever(self.storage, self.provider)
        self.reader = CodeReader(self.storage)
        self.navigator = CodeNavigator(self.storage)
        
        self.tools = self._create_tools()
        self.llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        self.system_prompt = """
            Sei un Senior Software Engineer esperto. 
            Rispondi usando SOLO i tool per esplorare la codebase.

            MANIFESTO:
            Noi crediamo nell'efficienza, fare meno passaggi/richieste possibili per giungere allo scopo richiesto ci permette di risparmiare energia elettrica e non inquinare
            
            STRATEGIA DI RICERCA OTTIMALE:
            1. 🧠 **Usa SEMPRE `search_codebase` PRIMA di esplorare file/cartelle.**
               - Se cerchi "dove inizia l'app", cerca "app" o "main" con filtro `role='entry_point'`.
               - Se cerchi definizioni di classi/funzioni, usa i filtri `role` o `type`.
               - NON usare `list_repo_structure` o `find_folder` a meno che la ricerca semantica non fallisca o tu debba esplorare la struttura fisica.
            
            2. 🔍 **Usa i Filtri di Ricerca**

            LINEE GUIDA:
            - Se devi sapere CHI CHIAMA una funzione/classe, o cosa essa chiama, DEVI usare `inspect_node_relationships` con l'UUID del nodo. NON affidarti solo alla ricerca testuale per le relazioni.
            - Se devi leggere l'implementazione completa, usa `read_file_content`.
            - Sii preciso. Se non trovi esattamente ciò che viene chiesto, riporta ciò che hai trovato e chiedi chiarimenti.
            - NON ripetere la stessa chiamata tool con gli stessi argomenti.
            """
        
        # In-memory checkpointer for this session
        self.checkpointer = MemorySaver()
        self.agent_executor = create_react_agent(self.llm, self.tools, checkpointer=self.checkpointer)

    def _create_tools(self):
        # We need to bind the repo_id to the tools or use a closure/method approach.
        # Since LangChain tools are functions, we can define them inside here or use partials.
        # Defining them as methods wrapped with @tool might be tricky with 'self'.
        # Let's define them as closures.

        @tool
        def search_codebase(query: str, filters: Optional[SearchFiltersInput] = None):
            """Cerca semanticamente nel codice."""
            filter_dict = filters.model_dump(exclude_none=True) if filters else None
            try:
                results = self.retriever.retrieve(query, repo_id=self.repo_id, limit=5, strategy="hybrid", filters=filter_dict)
                return "\n".join([r.render() for r in results]) if results else "Nessun risultato trovato."
            except Exception as e:
                return f"Errore ricerca: {e}"

        @tool
        def read_file_content(file_path: str, start_line: Optional[int] = None, end_line: Optional[int] = None):
            """Legge contenuto file."""
            try:
                data = self.reader.read_file(self.repo_id, file_path, start_line, end_line)
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
                parent = self.navigator.read_parent_chunk(node_id)
                if parent: 
                    report.append(f"⬆️ PARENT: {parent.get('type')} in {parent.get('file_path')}")
                else:
                    report.append("⬆️ PARENT: None (Top-level node)")

                # 2. Next
                nxt = self.navigator.read_neighbor_chunk(node_id, "next")
                if nxt: 
                    prev = nxt.get('content', '').split('\n')[0][:80]
                    report.append(f"➡️ NEXT: {nxt.get('type')} (ID: {nxt.get('id')})\n   Preview: {prev}...")
                    
                # 3. Impact
                impact = self.navigator.analyze_impact(node_id)
                if impact:
                    report.append(f"⬅️ CALLED BY ({len(impact)} refs):")
                    for i in impact[:5]:
                        report.append(f"   - {i['file']} L{i['line']} ({i['relation']})")
                else:
                    report.append("⬅️ CALLED BY: None")
                    
                # 4. Pipeline
                pipe = self.navigator.visualize_pipeline(node_id)
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
                dirs = self.reader.find_directories(self.repo_id, name_pattern)
                if not dirs:
                    return f"Nessuna cartella trovata contenente '{name_pattern}'."
                return "Cartelle trovate:\n" + "\n".join([f"- {d}" for d in dirs])
            except Exception as e:
                return f"Errore ricerca cartelle: {e}"

        @tool
        def list_repo_structure(path: str = "", max_depth: int = 2):
            """
            Elenca file e cartelle nella repository. 
            Usa questo tool per capire com'è organizzato il progetto (es. dove sono i source file, dove sono i test).
            """
            try:
                items = self.reader.list_directory(self.repo_id, path)
                output = [f"Listing '{path or '/'}':"]
                for item in items:
                    icon = "📁" if item['type'] == 'dir' else "📄"
                    output.append(f"{icon} {item['name']}")
                    
                    # Mini-esplorazione per profondità 2
                    if item['type'] == 'dir' and max_depth > 1:
                        try:
                            sub_items = self.reader.list_directory(self.repo_id, item['path'])
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

        return [search_codebase, read_file_content, inspect_node_relationships, list_repo_structure, find_folder]

    def stream_chat(self, message: str, thread_id: str):
        config = {"configurable": {"thread_id": thread_id}}
        inputs = {"messages": [SystemMessage(content=self.system_prompt), HumanMessage(content=message)]}
        
        # We use stream_mode="values" to get the full state, but for streaming to frontend we might want updates.
        # Actually, let's use the generator to yield events.
        # We want to yield:
        # 1. Tool calls (input)
        # 2. Tool outputs (results)
        # 3. Final answer chunks (or full answer)
        
        # LangGraph stream yields state updates.
        # Let's iterate and extract what we need.
        
        num_processed = 0
        try:
            for event in self.agent_executor.stream(inputs, config=config, stream_mode="values"):
                if "messages" not in event: continue
                current_messages = event["messages"]
                
                if len(current_messages) > num_processed:
                    new_msgs = current_messages[num_processed:]
                    num_processed = len(current_messages)
                    
                    for msg in new_msgs:
                        # Check if it's an AI message
                        if getattr(msg, "type", None) == "ai":
                            if getattr(msg, "tool_calls", None):
                                # It's a tool call
                                for tc in msg.tool_calls:
                                    logger.info(f"Yielding tool_call: {tc.get('name')} ID: {tc.get('id')}")
                                    yield json.dumps({
                                        "type": "tool_call",
                                        "name": tc.get('name'),
                                        "args": tc.get('args'),
                                        "id": tc.get('id')
                                    }) + "\n"
                            else:
                                # It's the final answer (or a thought)
                                yield json.dumps({
                                    "type": "message",
                                    "content": msg.content
                                }) + "\n"
                        
                        # Check if it's a Tool message (output)
                        elif getattr(msg, "type", None) == "tool":
                             logger.info(f"Yielding tool_output for ID: {msg.tool_call_id}")
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
