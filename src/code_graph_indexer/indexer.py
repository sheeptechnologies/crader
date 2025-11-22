import os
import logging
import concurrent.futures
from typing import Generator, Dict, Any, List
from .parsing.parser import TreeSitterRepoParser
from .graph.indexers.scip import SCIPIndexer, SCIPRunner
from .graph.builder import KnowledgeGraphBuilder
from .storage.sqlite import SqliteGraphStorage

logger = logging.getLogger(__name__)

class CodebaseIndexer:
    def __init__(self, repo_path: str):
        self.repo_path = os.path.abspath(repo_path)
        if not os.path.isdir(self.repo_path):
            raise ValueError(f"Path not found: {self.repo_path}")
        
        self.parser = TreeSitterRepoParser(self.repo_path)
        self.scip_indexer = SCIPIndexer(self.repo_path)
        self.scip_runner = SCIPRunner(self.repo_path)
        
        self.storage = SqliteGraphStorage()
        self.builder = KnowledgeGraphBuilder(self.storage)

    def index(self):
        logger.info(f"🚀 Indexing: {self.repo_path}")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future_scip = executor.submit(self.scip_runner.run_to_disk)
            
            files_count = 0
            # Streaming: (File, Nodes, Contents, Relations)
            for f_rec, nodes, contents_list, rels_list in self.parser.stream_semantic_chunks():
                
                # 1. Salva FILE
                self.builder.add_files([f_rec])
                
                # 2. Salva NODI (ChunkNode)
                self.builder.add_chunks(nodes)
                
                # 3. Salva CONTENUTI (ChunkContent)
                self.builder.add_contents(contents_list)
                
                # 4. Salva RELAZIONI STRUTTURALI (child_of)
                if rels_list:
                    self.builder.add_relations(rels_list)
                
                files_count += 1
            
            logger.info(f"Parsing completato: {files_count} file processati.")
            scip_json = future_scip.result()

        # 5. FASE LINKING SCIP
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
        stats = self.storage.get_stats()
        logger.info(f"✅ Indexing completato. Staging Area pronta: {stats}")

    # --- API ---
    def get_nodes(self): return self.storage.get_all_nodes()
    def get_contents(self): return self.storage.get_all_contents()
    def get_edges(self): return self.storage.get_all_edges()
    def get_files(self): return self.storage.get_all_files() # Utile per debug
    def get_stats(self): return self.storage.get_stats()
    def close(self): self.storage.close()
    def __del__(self): self.close()