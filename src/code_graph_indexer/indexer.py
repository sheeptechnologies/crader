import os
import logging
import concurrent.futures
from typing import Generator, Dict, Any, List, Optional
from .parsing.parser import TreeSitterRepoParser
from .graph.indexers.scip import SCIPIndexer, SCIPRunner
from .graph.builder import KnowledgeGraphBuilder
from .storage.postgres import PostgresGraphStorage
from .embedding.embedder import CodeEmbedder
from .providers.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)

class CodebaseIndexer:
    def __init__(self, repo_path: str, storage: PostgresGraphStorage):
        self.repo_path = os.path.abspath(repo_path)
        if not os.path.isdir(self.repo_path):
            raise ValueError(f"Path not found: {self.repo_path}")
        
        self.storage = storage
        self.parser = TreeSitterRepoParser(self.repo_path)
        self.scip_indexer = SCIPIndexer(self.repo_path)
        self.scip_runner = SCIPRunner(self.repo_path)
        self.builder = KnowledgeGraphBuilder(self.storage)

    def index(self, force: bool = False) -> str:
        """
        Esegue l'indicizzazione atomica con strategia Blue-Green.
        Returns:
            str: L'ID dello snapshot attivo (nuovo o esistente).
        """
        logger.info(f"🚀 Indexing Request: {self.repo_path}")
        
        # 1. Recupero Metadati
        repo_info = self.parser.metadata_provider.get_repo_info()
        url = repo_info['url']
        branch = repo_info['branch']
        commit = repo_info['commit_hash']
        name = repo_info['name']
        
        # 2. Identity (Repo Stabile)
        repo_id = self.storage.ensure_repository(url, branch, name)
        
        # Recuperiamo lo snapshot ATTUALMENTE attivo (Blue) per eventuale pulizia post-switch
        previous_live_snapshot = self.storage.get_active_snapshot_id(repo_id)
        
        # 3. Creazione Snapshot Candidato (Green)
        # Se force=True, passiamo force_new=True per generare un NUOVO UUID e non toccare quello attivo
        snapshot_id, is_new = self.storage.create_snapshot(
            repo_id, 
            commit, 
            force_new=force 
        )
        
        # Idempotenza: Se non è nuovo e non forziamo, usiamo quello esistente
        if not is_new and not force:
            logger.info(f"✅ Snapshot già valido ({snapshot_id}). Nessuna azione.")
            return snapshot_id

        logger.info(f"🏗️  Building Snapshot {snapshot_id} (Candidate)...")
        
        # Configurazione Parser sul candidato
        self.parser.snapshot_id = snapshot_id
        self.parser.repo_info['commit_hash'] = commit 

        # Inizializzazione Manifest in-memory
        manifest_tree = {"type": "dir", "children": {}}

        def add_to_manifest(path: str):
            parts = path.split('/')
            current = manifest_tree
            for i, part in enumerate(parts):
                is_file = (i == len(parts) - 1)
                if part not in current["children"]:
                    current["children"][part] = {
                        "type": "file" if is_file else "dir",
                        "children": {} if not is_file else None
                    }
                current = current["children"][part]

        try:
            # 4. Populate (Scrittura Isolata)
            logger.info(f"🔨 Inizio popolamento dati su {snapshot_id}...")
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future_scip = executor.submit(self.scip_runner.run_to_disk)
                
                files_count = 0
                nodes_count = 0
                
                # Parsing Stream
                for f_rec, nodes, contents_list, rels_list in self.parser.stream_semantic_chunks():
                    # Scrittura DB
                    self.builder.add_files([f_rec])
                    self.builder.add_chunks(nodes)
                    self.builder.add_contents(contents_list) 
                    
                    contents_map = {c.chunk_hash: c for c in contents_list}
                    self.builder.index_search_content(nodes, contents_map)
                    
                    if rels_list:
                        self.builder.add_relations(rels_list, snapshot_id=snapshot_id) 
                    
                    # Costruzione Manifest
                    add_to_manifest(f_rec.path)
                    
                    files_count += 1
                    nodes_count += len(nodes)
                    
                    if files_count % 50 == 0:
                        logger.info(f"⏳ Progress: {files_count} files processed...")
                
                logger.info(f"✅ Parsing Tree-sitter completato: {files_count} file, {nodes_count} nodi.")
                
                # SCIP Processing
                logger.info("⏳ Waiting for SCIP indexer...")
                scip_json = future_scip.result()
                
                if scip_json:
                    logger.info("🔗 Linking relazioni SCIP...")
                    rel_gen = self.scip_indexer.stream_relations_from_file(scip_json)
                    batch = []
                    for rel in rel_gen:
                        batch.append(rel)
                        if len(batch) >= 5000:
                            self.builder.add_relations(batch, snapshot_id=snapshot_id)
                            batch = []
                    if batch: 
                        self.builder.add_relations(batch, snapshot_id=snapshot_id)
                    
                    try: os.remove(scip_json)
                    except: pass
            
            # 5. ATOMIC ACTIVATION (Switch)
            # Solo ora il nuovo snapshot diventa visibile agli agenti
            stats = {"files": files_count, "nodes": nodes_count, "engine": "v2_immutable"}
            self.storage.activate_snapshot(repo_id, snapshot_id, stats, manifest=manifest_tree)
            logger.info(f"🚀 SNAPSHOT ACTIVATED: {snapshot_id} è ora LIVE.")
            
            # 6. GARBAGE COLLECTION (Safe Pruning)
            # Se avevamo un vecchio snapshot attivo diverso da quello nuovo, lo cancelliamo
            if previous_live_snapshot and previous_live_snapshot != snapshot_id:
                logger.info(f"🧹 GC: Rimozione vecchio snapshot obsoleto {previous_live_snapshot}...")
                self.storage.prune_snapshot(previous_live_snapshot)
            
            return snapshot_id

        except Exception as e:
            logger.error(f"❌ Indexing Failed on Candidate {snapshot_id}: {e}")
            
            # FALLBACK SICURO:
            # Marchiamo il candidato come failed. 
            # Il puntatore della repo NON cambia (resta su previous_live_snapshot).
            # Il servizio continua a funzionare con la versione vecchia.
            self.storage.fail_snapshot(snapshot_id, str(e))
            
            # Opzionale: Pulizia immediata del candidato fallito
            # self.storage.prune_snapshot(snapshot_id)
            
            raise e
    def embed(self, provider: EmbeddingProvider, batch_size: int = 32, debug: bool = False, force_snapshot_id: str = None):
        logger.info(f"🤖 Avvio Embedding con {provider.model_name}")
        
        repo_info = self.parser.metadata_provider.get_repo_info()
        url = repo_info['url']
        branch = repo_info['branch']
        
        target_snapshot_id = force_snapshot_id
        if not target_snapshot_id:
             repo_id = self.storage.ensure_repository(url, branch, repo_info['name'])
             target_snapshot_id = self.storage.get_active_snapshot_id(repo_id)
        
        if not target_snapshot_id:
             raise ValueError(f"Nessuno snapshot attivo per {url}. Esegui prima index()!")
        
        logger.info(f"Targeting Snapshot: {target_snapshot_id}")

        embedder = CodeEmbedder(self.storage, provider)
        
        yield from embedder.run_indexing(
            snapshot_id=target_snapshot_id,
            batch_size=batch_size, 
            yield_debug_docs=debug
        )

    def get_stats(self): return self.storage.get_stats()