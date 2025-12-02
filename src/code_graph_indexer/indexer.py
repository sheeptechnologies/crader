import os
import logging
import concurrent.futures
from typing import Generator, Dict, Any, List, Optional
from .parsing.parser import TreeSitterRepoParser
from .graph.indexers.scip import SCIPIndexer, SCIPRunner
from .graph.builder import KnowledgeGraphBuilder
from .storage.base import GraphStorage
from .embedding.embedder import CodeEmbedder
from .providers.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)

class CodebaseIndexer:
    def __init__(self, repo_path: str, storage: GraphStorage):
        self.repo_path = os.path.abspath(repo_path)
        if not os.path.isdir(self.repo_path):
            raise ValueError(f"Path not found: {self.repo_path}")
        
        self.storage = storage
        self.parser = TreeSitterRepoParser(self.repo_path)
        self.scip_indexer = SCIPIndexer(self.repo_path)
        self.scip_runner = SCIPRunner(self.repo_path)
        self.builder = KnowledgeGraphBuilder(self.storage)

    def index(self, force: bool = False):
        logger.info(f"🚀 Indexing: {self.repo_path}")
        
        repo_info = self.parser.metadata_provider.get_repo_info()
        url = repo_info['url']; branch = repo_info['branch']
        commit = repo_info['commit_hash']; name = repo_info['name']
        
        existing_repo = None
        if hasattr(self.storage, 'get_repository_by_context'):
            existing_repo = self.storage.get_repository_by_context(url, branch)
        
        internal_repo_id = None
        if existing_repo:
            internal_repo_id = existing_repo['id']
            last_commit = existing_repo.get('last_commit')
            status = existing_repo.get('status')
            
            if not force and status == 'completed' and last_commit == commit:
                logger.info(f"✅ Repository già aggiornata ({last_commit[:8]}). Skipping.")
                self.parser.repo_id = internal_repo_id
                return 
            
            if status == 'indexing' and not force:
                logger.warning("⚠️  Stato 'indexing' rilevato. Riavvio forzato.")
        
        internal_repo_id = self.storage.register_repository(
            id=internal_repo_id, name=name, url=url, branch=branch, 
            commit_hash=commit, local_path=self.repo_path
        )
        self.parser.repo_id = internal_repo_id
        logger.info(f"📋 Context: {url} | Branch: {branch} -> DB ID: {internal_repo_id}")
        
        logger.info(f"🧹 Pulizia storage per ID: {internal_repo_id}")
        self.storage.delete_previous_data(internal_repo_id, branch)
        
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future_scip = executor.submit(self.scip_runner.run_to_disk)
                
                files_count = 0
                for f_rec, nodes, contents_list, rels_list in self.parser.stream_semantic_chunks():
                    self.builder.add_files([f_rec])
                    self.builder.add_chunks(nodes)
                    self.builder.add_contents(contents_list)
                    
                    # [NEW] Popolamento Indice Ricerca Unificato
                    # Creiamo una mappa veloce per l'helper
                    contents_map = {c.chunk_hash: c for c in contents_list}
                    self.builder.index_search_content(nodes, contents_map)
                    
                    if rels_list:
                        self.builder.add_relations(rels_list, repo_id=internal_repo_id)
                    files_count += 1
                
                logger.info(f"Parsing completato: {files_count} file processati.")
                scip_json = future_scip.result()

            if scip_json:
                logger.info("Linking relazioni SCIP...")
                rel_gen = self.scip_indexer.stream_relations_from_file(scip_json)
                batch = []
                for rel in rel_gen:
                    batch.append(rel)
                    if len(batch) >= 5000:
                        self.builder.add_relations(batch, repo_id=internal_repo_id)
                        batch = []
                if batch: 
                    self.builder.add_relations(batch, repo_id=internal_repo_id)
                try: os.remove(scip_json)
                except: pass
            else:
                logger.warning("SCIP non ha prodotto output.")

            self.storage.commit()
            self.storage.update_repository_status(internal_repo_id, 'completed', commit)
            logger.info(f"✅ Indexing completato. Stats: {self.storage.get_stats()}")

        except Exception as e:
            logger.error(f"❌ Indexing fallito: {e}")
            self.storage.update_repository_status(internal_repo_id, 'failed', commit)
            raise e

    def embed(self, provider: EmbeddingProvider, batch_size: int = 32, debug: bool = False):
        logger.info(f"🤖 Avvio Embedding con {provider.model_name}")
        repo_info = self.parser.metadata_provider.get_repo_info()
        url = repo_info['url']; branch = repo_info['branch']
        
        repo_record = None
        if hasattr(self.storage, 'get_repository_by_context'):
            repo_record = self.storage.get_repository_by_context(url, branch)

        if not repo_record:
             raise ValueError(f"Repository {url} ({branch}) non trovata nel DB.")
             
        internal_repo_id = repo_record['id']
        embedder = CodeEmbedder(self.storage, provider)
        
        yield from embedder.run_indexing(
            repo_id=internal_repo_id, 
            branch=branch, 
            batch_size=batch_size, 
            yield_debug_docs=debug
        )

    # API Proxy
    def get_nodes(self): return self.storage.get_all_nodes()
    def get_contents(self): return self.storage.get_all_contents()
    def get_edges(self): return self.storage.get_all_edges()
    def get_files(self): return self.storage.get_all_files() 
    def get_stats(self): return self.storage.get_stats()