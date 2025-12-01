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
        
        # 1. Recupero metadati dal path locale
        repo_info = self.parser.metadata_provider.get_repo_info()
        url = repo_info['url']
        branch = repo_info['branch']
        commit = repo_info['commit_hash']
        name = repo_info['name']
        
        # 2. Check Preliminare (READ-ONLY)
        # Cerchiamo se esiste già un record per questo URL+Branch senza modificarlo
        existing_repo = None
        if hasattr(self.storage, 'get_repository_by_context'):
            existing_repo = self.storage.get_repository_by_context(url, branch)
        
        internal_repo_id = None

        # 3. Decisione: Skippare o Procedere?
        if existing_repo:
            internal_repo_id = existing_repo['id']
            last_commit = existing_repo.get('last_commit')
            status = existing_repo.get('status')
            
            # Se non forziamo e lo stato è coerente, usciamo
            if not force and status == 'completed' and last_commit == commit:
                logger.info(f"✅ Repository già aggiornata (Commit: {last_commit[:8]}). Skipping index.")
                # Assicuriamoci che il parser abbia l'ID corretto anche se skippiamo
                self.parser.repo_id = internal_repo_id
                return 
            
            if status == 'indexing' and not force:
                logger.warning("⚠️  Stato 'indexing' rilevato (possibile crash precedente). Riavvio forzato.")
        
        # 4. Registrazione / Aggiornamento Stato (WRITE)
        # Se siamo qui, dobbiamo indicizzare. Ora è sicuro settare status='indexing'.
        internal_repo_id = self.storage.register_repository(
            id=internal_repo_id, # Se None, ne crea uno nuovo
            name=name,
            url=url,
            branch=branch,
            commit_hash=commit,
            local_path=self.repo_path
        )
        
        # INIEZIONE FONDAMENTALE
        self.parser.repo_id = internal_repo_id
        
        logger.info(f"📋 Context: {url} | Branch: {branch} -> DB ID: {internal_repo_id}")
        
        # 5. Pulizia e Preparazione
        logger.info(f"🧹 Pulizia storage per ID: {internal_repo_id}")
        self.storage.delete_previous_data(internal_repo_id, branch)
        
        # 6. Esecuzione Indexing
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future_scip = executor.submit(self.scip_runner.run_to_disk)
                
                files_count = 0
                for f_rec, nodes, contents_list, rels_list in self.parser.stream_semantic_chunks():
                    # f_rec ha già repo_id aggiornato perché abbiamo settato parser.repo_id
                    self.builder.add_files([f_rec])
                    self.builder.add_chunks(nodes)
                    self.builder.add_contents(contents_list)
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
            
            stats = self.storage.get_stats()
            logger.info(f"✅ Indexing completato. Stats: {stats}")

        except Exception as e:
            logger.error(f"❌ Indexing fallito: {e}")
            self.storage.update_repository_status(internal_repo_id, 'failed', commit)
            raise e

    def embed(self, provider: EmbeddingProvider, batch_size: int = 32, debug: bool = False):
        """
        Avvia la generazione degli embedding.
        Recupera repo_id e branch attuali per garantire coerenza.
        """
        logger.info(f"🤖 Avvio Embedding con {provider.model_name}")
        
        repo_info = self.parser.metadata_provider.get_repo_info()
        url = repo_info['url']
        branch = repo_info['branch']
        
        # Recuperiamo l'ID interno dal DB basandoci sul contesto
        if hasattr(self.storage, 'get_repository_by_context'):
            repo_record = self.storage.get_repository_by_context(url, branch)
        else:
            # Fallback se lo storage non supporta il metodo (es. mock)
            repo_record = None

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