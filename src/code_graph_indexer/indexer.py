import os
import logging
import concurrent.futures
from typing import Generator, Dict, Any, List, Optional
from .parsing.parser import TreeSitterRepoParser
from .graph.indexers.scip import SCIPIndexer, SCIPRunner
from .graph.builder import KnowledgeGraphBuilder
from .storage.sqlite import SqliteGraphStorage
from .embedding.embedder import CodeEmbedder
from .providers.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)

class CodebaseIndexer:
    def __init__(self, repo_path: str, db_path: str = "sheep_index.db"):
        """
        :param repo_path: Path della cartella sorgente.
        :param db_path: Path del file DB persistente.
        """
        self.repo_path = os.path.abspath(repo_path)
        if not os.path.isdir(self.repo_path):
            raise ValueError(f"Path not found: {self.repo_path}")
        
        self.parser = TreeSitterRepoParser(self.repo_path)
        self.scip_indexer = SCIPIndexer(self.repo_path)
        self.scip_runner = SCIPRunner(self.repo_path)
        
        # Storage persistente
        self.storage = SqliteGraphStorage(db_path=db_path)
        self.builder = KnowledgeGraphBuilder(self.storage)

    def index(self, force: bool = False):
        """
        Esegue l'indicizzazione.
        :param force: Se True, forza la re-indicizzazione anche se la repo è già aggiornata.
        """
        logger.info(f"🚀 Indexing: {self.repo_path}")
        
        # 1. Recupera Info Repo (ID canonico)
        # Nota: TreeSitterRepoParser internamente ha già istanziato un MetadataProvider,
        # ma per pulizia ne usiamo uno qui o accediamo a quello del parser.
        # Accediamo a quello del parser per coerenza.
        repo_info = self.parser.metadata_provider.get_repo_info()
        repo_id = repo_info['repo_id']
        current_commit = repo_info['commit_hash']
        
        logger.info(f"📋 Repo ID: {repo_id} | Commit: {current_commit}")

        # 2. Check Stato Esistente (Persistenza)
        existing_repo = self.storage.get_repository(repo_id)
        
        if existing_repo and not force:
            last_commit = existing_repo.get('last_commit')
            status = existing_repo.get('status')
            
            if status == 'completed' and last_commit == current_commit:
                logger.info(f"✅ Repository già aggiornata ({last_commit}). Skipping index.")
                return # ESCI SUBITO, TUTTO FATTO
            
            if status == 'indexing':
                logger.warning("⚠️  Repository in stato 'indexing'. Possibile crash precedente. Riavvio forzato.")
        
        # 3. Registra Inizio Lavoro
        self.storage.register_repository(
            repo_id, repo_info['name'], repo_info['url'], repo_info['branch'], current_commit
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
            
            # 4. Aggiorna Stato Finale
            self.storage.update_repository_status(repo_id, 'completed', current_commit)
            
            stats = self.storage.get_stats()
            logger.info(f"✅ Indexing completato. Stats: {stats}")

        except Exception as e:
            logger.error(f"❌ Indexing fallito: {e}")
            self.storage.update_repository_status(repo_id, 'failed', current_commit)
            raise e

    def embed(self, provider: EmbeddingProvider, batch_size: int = 32, debug: bool = False):
        logger.info(f"🤖 Avvio Embedding con {provider.model_name}")
        embedder = CodeEmbedder(self.storage, provider)
        yield from embedder.run_indexing(batch_size=batch_size, yield_debug_docs=debug)

    # --- API ---
    def get_nodes(self): return self.storage.get_all_nodes()
    def get_contents(self): return self.storage.get_all_contents()
    def get_edges(self): return self.storage.get_all_edges()
    def get_files(self): return self.storage.get_all_files() 
    def get_stats(self): return self.storage.get_stats()
    def close(self): self.storage.close()
    def __del__(self): self.close()