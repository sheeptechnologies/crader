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
        logger.info(f"🚀 Indexing Request: {self.repo_path}")
        
        # 1. Recupero metadati iniziali
        repo_info = self.parser.metadata_provider.get_repo_info()
        url = repo_info['url']
        branch = repo_info['branch']
        commit = repo_info['commit_hash']
        name = repo_info['name']
        
        # 2. ACQUIRE LOCK (o Accoda)
        locked, internal_repo_id = self.storage.acquire_indexing_lock(
            url=url, branch=branch, name=name, 
            commit_hash=commit, local_path=self.repo_path
        )
        
        if not locked:
            logger.info(f"✋ Indexing demandata alla coda (Repo occupato). Exiting.")
            return

        self.parser.repo_id = internal_repo_id
        logger.info(f"📋 Context: {url} | Branch: {branch} -> DB ID: {internal_repo_id}")
        
        # 3. WORKER LOOP (Processa finché c'è lavoro in coda)
        current_work_commit = commit
        
        while True:
            try:
                logger.info(f"🔨 Processing Commit: {current_work_commit}")
                
                # [FIX CRITICO] Aggiorniamo il commit nel parser!
                # Altrimenti i FileRecord verrebbero taggati con il vecchio hash cachato all'init.
                if hasattr(self.parser, 'repo_info'):
                    self.parser.repo_info['commit_hash'] = current_work_commit
                
                # --- PIPELINE START ---
                logger.info(f"🧹 Pulizia storage per ID: {internal_repo_id}")
                self.storage.delete_previous_data(internal_repo_id, branch)
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future_scip = executor.submit(self.scip_runner.run_to_disk)
                    
                    files_count = 0
                    for f_rec, nodes, contents_list, rels_list in self.parser.stream_semantic_chunks():
                        self.builder.add_files([f_rec])
                        self.builder.add_chunks(nodes)
                        self.builder.add_contents(contents_list)
                        
                        contents_map = {c.chunk_hash: c for c in contents_list}
                        self.builder.index_search_content(nodes, contents_map)
                        
                        if rels_list:
                            self.builder.add_relations(rels_list, repo_id=internal_repo_id)
                        files_count += 1
                        
                        if files_count % 50 == 0:
                            logger.info(f"⏳ Progress: {files_count} files processed...")
                            
                    logger.info(f"✅ Parsing completato: {files_count} file processati.")
                    
                    logger.info("⏳ Waiting for SCIP indexer...")
                    scip_json = future_scip.result()
                    logger.info(f"✅ SCIP Indexing finished. Output: {scip_json}")
                
                if scip_json:
                    logger.info("Linking relazioni SCIP...")
                    rel_gen = self.scip_indexer.stream_relations_from_file(scip_json)
                    batch = []
                    for rel in rel_gen:
                        batch.append(rel)
                        if len(batch) >= 5000:
                            self.builder.add_relations(batch, repo_id=internal_repo_id)
                            batch = []
                    if batch: self.builder.add_relations(batch, repo_id=internal_repo_id)
                    try: os.remove(scip_json)
                    except: pass
                
                self.storage.commit()
                # --- PIPELINE END ---

                # 4. CHECK & RELEASE (Unified Logic)
                next_commit = self.storage.release_indexing_lock(
                    internal_repo_id, 
                    success=True, 
                    commit_hash=current_work_commit
                )
                
                if next_commit:
                    # C'è nuovo lavoro! Aggiorniamo la variabile e ripetiamo il loop
                    current_work_commit = next_commit
                    continue 
                else:
                    stats = self.storage.get_stats()
                    logger.info(f"✅ Ciclo completato. Stats finali: {stats}")
                    break

            except Exception as e:
                logger.error(f"❌ Indexing fallito per commit {current_work_commit}: {e}")
                # Rilascio forzato in errore (success=False)
                self.storage.release_indexing_lock(internal_repo_id, success=False)
                raise e

    def embed(self, provider: EmbeddingProvider, batch_size: int = 32, debug: bool = False):
        """
        Avvia la generazione degli embedding.
        """
        logger.info(f"🤖 Avvio Embedding con {provider.model_name}")
        
        repo_info = self.parser.metadata_provider.get_repo_info()
        url = repo_info['url']
        branch = repo_info['branch']
        
        # Recuperiamo l'ID interno dal DB
        repo_record = None
        if hasattr(self.storage, 'get_repository_by_context'):
            repo_record = self.storage.get_repository_by_context(url, branch)

        if not repo_record:
             raise ValueError(f"Repository {url} ({branch}) non trovata nel DB. Esegui prima index()!")
             
        internal_repo_id = repo_record['id']
        
        embedder = CodeEmbedder(self.storage, provider)
        
        yield from embedder.run_indexing(
            repo_id=internal_repo_id, 
            branch=branch, 
            batch_size=batch_size, 
            yield_debug_docs=debug
        )

    # --- API Proxy ---
    def get_nodes(self): return self.storage.get_all_nodes()
    def get_contents(self): return self.storage.get_all_contents()
    def get_edges(self): return self.storage.get_all_edges()
    def get_files(self): return self.storage.get_all_files() 
    def get_stats(self): return self.storage.get_stats()