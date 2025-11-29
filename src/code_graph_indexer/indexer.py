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
        """
        Inizializza l'indicizzatore.
        :param repo_path: Path della cartella sorgente.
        :param storage: Istanza di GraphStorage (SQLite, Postgres, ecc.).
        """
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
        repo_id = repo_info['repo_id']
        current_commit = repo_info['commit_hash']
        current_branch = repo_info['branch']
        
        logger.info(f"📋 Repo: {repo_id[:8]}... | Branch: {current_branch} | Commit: {current_commit[:8]}...")

        existing_repo = self.storage.get_repository(repo_id)
        
        if existing_repo and not force:
            last_commit = existing_repo.get('last_commit')
            status = existing_repo.get('status')
            
            if status == 'completed' and last_commit == current_commit:
                logger.info(f"✅ Repository già aggiornata ({last_commit}). Skipping index.")
                return 
            
            if status == 'indexing':
                logger.warning("⚠️  Stato 'indexing' rilevato. Riavvio forzato.")
        
        logger.info(f"🧹 Preparazione storage per branch: {current_branch}")
        self.storage.delete_previous_data(repo_id, current_branch)
        
        self.storage.register_repository(
            repo_id, repo_info['name'], repo_info['url'], current_branch, current_commit
        )

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future_scip = executor.submit(self.scip_runner.run_to_disk)
                
                files_count = 0
                for f_rec, nodes, contents_list, rels_list in self.parser.stream_semantic_chunks():
                    self.builder.add_files([f_rec])
                    self.builder.add_chunks(nodes)
                    self.builder.add_contents(contents_list)
                    if rels_list:
                        self.builder.add_relations(rels_list)
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
                        self.builder.add_relations(batch)
                        batch = []
                if batch: self.builder.add_relations(batch)
                try: os.remove(scip_json)
                except: pass
            else:
                logger.warning("SCIP non ha prodotto output.")

            self.storage.commit()
            self.storage.update_repository_status(repo_id, 'completed', current_commit)
            
            stats = self.storage.get_stats()
            logger.info(f"✅ Indexing completato. Stats: {stats}")

        except Exception as e:
            logger.error(f"❌ Indexing fallito: {e}")
            self.storage.update_repository_status(repo_id, 'failed', current_commit)
            raise e

    def embed(self, provider: EmbeddingProvider, batch_size: int = 32, debug: bool = False):
        """
        Avvia la generazione degli embedding.
        Recupera repo_id e branch attuali per garantire coerenza.
        """
        logger.info(f"🤖 Avvio Embedding con {provider.model_name}")
        
        repo_info = self.parser.metadata_provider.get_repo_info()
        repo_id = repo_info['repo_id']
        current_branch = repo_info['branch']
        
        embedder = CodeEmbedder(self.storage, provider)
        
        yield from embedder.run_indexing(
            repo_id=repo_id, 
            branch=current_branch, 
            batch_size=batch_size, 
            yield_debug_docs=debug
        )

    # --- API Proxy ---
    def get_nodes(self): return self.storage.get_all_nodes()
    def get_contents(self): return self.storage.get_all_contents()
    def get_edges(self): return self.storage.get_all_edges()
    def get_files(self): return self.storage.get_all_files() 
    def get_stats(self): return self.storage.get_stats()