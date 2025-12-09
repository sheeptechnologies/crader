import os
import logging
import concurrent.futures
from typing import Dict, Any, List, Optional
from datetime import datetime

# Componenti Interni
from .volume_manager.git_volume_manager import GitVolumeManager
from .parsing.parser import TreeSitterRepoParser
from .graph.indexers.scip import SCIPIndexer, SCIPRunner
from .graph.builder import KnowledgeGraphBuilder
from .storage.postgres import PostgresGraphStorage
from .embedding.embedder import CodeEmbedder
from .providers.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)

class CodebaseIndexer:
    def __init__(
        self, 
        repo_url: str, 
        branch: str, 
        storage: PostgresGraphStorage
    ):
        """
        Inizializza l'indexer per una specifica repo remota.
        Non richiede che il codice sia presente localmente all'avvio.
        """
        self.repo_url = repo_url
        self.branch = branch
        self.storage = storage
        
        # Gestore dei volumi Git (Cache + Worktrees)
        self.git_manager = GitVolumeManager()
        
        # Builder è stateless rispetto al path, quindi possiamo inizializzarlo qui
        self.builder = KnowledgeGraphBuilder(self.storage)

    def index(self, force: bool = False) -> str:
        """
        Entry point del processo di indicizzazione.
        Implementa il pattern 'Greedy Worker Loop' (Debouncing).
        
        Returns:
            str: ID dello snapshot attivo (o "queued" se accodato).
        """
        logger.info(f"🚀 Indexing Request Start: {self.repo_url} ({self.branch})")
        
        # 1. Identity Resolution
        # Estraiamo il nome dal URL per consistenza (es. github.com/user/repo.git -> repo)
        repo_name = self.repo_url.rstrip('/').split('/')[-1].replace('.git', '')
        
        repo_id = self.storage.ensure_repository(
            url=self.repo_url, 
            branch=self.branch, 
            name=repo_name
        )
        
        active_snapshot_id = None
        first_run = True

        # === GREEDY WORKER LOOP ===
        # Questo ciclo continua finché ci sono richieste pendenti ('reindex_requested_at')
        while True:
            # Fase A: Network Sync (Heavy Lifting)
            # Scarichiamo/Aggiorniamo la cache centrale (Bare Repo)
            logger.info("🌍 Syncing repository cache...")
            self.git_manager.ensure_repo_updated(self.repo_url)
            
            # Identifichiamo il target commit DOPO il fetch
            commit = self.git_manager.get_head_commit(self.repo_url, self.branch)
            logger.info(f"🎯 Targeting Commit: {commit}")

            # Fase B: Lock & Concurrency Check
            # Proviamo a creare lo snapshot. Se fallisce per UniqueViolation, 
            # significa che un altro worker sta lavorando -> accodiamo.
            snapshot_id, is_new = self.storage.create_snapshot(repo_id, commit, force_new=force)
            
            # CASO 1: Repo Occupata
            if not is_new and snapshot_id is None:
                logger.info("⏸️  Indicizzazione accodata (Debounced).")
                return "queued"
            
            # CASO 2: Idempotenza (Snapshot già pronto)
            if not is_new and snapshot_id and not force:
                logger.info(f"✅ Snapshot {snapshot_id} già valido.")
                return snapshot_id

            # CASO 3: Lock Acquisito -> Inizio Lavoro
            active_snapshot_id = snapshot_id
            
            try:
                # Fase C: Esecuzione in Ambiente Effimero (Isolation)
                # Creiamo il worktree SOLO per la durata del parsing
                with self.git_manager.ephemeral_worktree(self.repo_url, commit) as worktree_path:
                    logger.info(f"⚙️  Worktree montato in: {worktree_path}")
                    
                    self._run_indexing_pipeline(
                        repo_id=repo_id,
                        snapshot_id=snapshot_id,
                        commit=commit,
                        worktree_path=worktree_path
                    )
                # Uscendo dal 'with', il worktree viene distrutto. Disco pulito.

                # Fase D: Check Tail (Debounce Logic)
                # Abbiamo finito. C'è qualcuno che ha bussato nel frattempo?
                if self.storage.check_and_reset_reindex_flag(repo_id):
                    logger.info("🔁 Dirty Flag trovato! Rilevata nuova richiesta. Riavvio loop...")
                    first_run = False
                    force = True # Forziamo la creazione del prossimo snapshot
                    continue # Ripartiamo da 'Fase A' con la nuova HEAD
                else:
                    logger.info("✅ Loop terminato. Nessuna richiesta pendente.")
                    break

            except Exception as e:
                logger.error(f"❌ Indexing Failed on {snapshot_id}: {e}", exc_info=True)
                self.storage.fail_snapshot(snapshot_id, str(e))
                # In caso di errore critico, usciamo dal loop per evitare cicli infiniti di failure
                raise e
        
        return active_snapshot_id

    def _run_indexing_pipeline(self, repo_id: str, snapshot_id: str, commit: str, worktree_path: str):
        """
        Esegue la logica di parsing e analisi statica su un path temporaneo specifico.
        """
        # 1. Istanziazione Componenti Scoped (Legati al path temporaneo)
        parser = TreeSitterRepoParser(repo_path=worktree_path)
        # Iniettiamo ID e Commit manualmente perché il parser non può dedurli dal worktree detached
        parser.snapshot_id = snapshot_id
        parser.repo_info = {
            'commit_hash': commit,
            'url': self.repo_url,
            'branch': self.branch,
            'name': self.repo_url.split('/')[-1]
        }

        scip_runner = SCIPRunner(repo_path=worktree_path)
        scip_indexer = SCIPIndexer(repo_path=worktree_path)

        # Recuperiamo snapshot precedente per GC
        previous_live_snapshot = self.storage.get_active_snapshot_id(repo_id)

        # Helper per Manifest
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

        # 2. Esecuzione Parallela (Parsing + SCIP)
        logger.info(f"🔨 Parsing started for {snapshot_id}...")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            # Avviamo SCIP in background (processo esterno Go/Node)
            future_scip = executor.submit(scip_runner.run_to_disk)
            
            files_count = 0
            nodes_count = 0
            
            # Stream Parsing Tree-sitter
            for f_rec, nodes, contents_list, rels_list in parser.stream_semantic_chunks():
                # Commit DB
                self.builder.add_files([f_rec])
                self.builder.add_chunks(nodes)
                self.builder.add_contents(contents_list) 
                
                # Full Text Search Indexing
                contents_map = {c.chunk_hash: c for c in contents_list}
                self.builder.index_search_content(nodes, contents_map)
                
                # Relazioni base (Tree-sitter)
                if rels_list:
                    self.builder.add_relations(rels_list, snapshot_id=snapshot_id) 
                
                add_to_manifest(f_rec.path)
                
                files_count += 1
                nodes_count += len(nodes)
                
                if files_count % 100 == 0:
                    logger.info(f"⏳ Parsed {files_count} files...")

            # 3. Integrazione SCIP (LSIF)
            scip_json = future_scip.result()
            if scip_json and os.path.exists(scip_json):
                logger.info("🔗 Ingesting SCIP relations...")
                rel_gen = scip_indexer.stream_relations_from_file(scip_json)
                
                batch = []
                for rel in rel_gen:
                    batch.append(rel)
                    if len(batch) >= 5000:
                        self.builder.add_relations(batch, snapshot_id=snapshot_id)
                        batch = []
                if batch: 
                    self.builder.add_relations(batch, snapshot_id=snapshot_id)
                
                # Cleanup file SCIP temporaneo
                os.remove(scip_json)

        # 4. Attivazione Atomica (Switch Live)
        stats = {
            "files": files_count, 
            "nodes": nodes_count, 
            "engine": "v3_ephemeral_worker"
        }
        self.storage.activate_snapshot(repo_id, snapshot_id, stats, manifest=manifest_tree)
        logger.info(f"🚀 SNAPSHOT ACTIVATED: {snapshot_id}")

        # 5. Garbage Collection
        if previous_live_snapshot and previous_live_snapshot != snapshot_id:
            logger.info(f"🧹 Pruning old snapshot {previous_live_snapshot}...")
            self.storage.prune_snapshot(previous_live_snapshot)

    def embed(self, provider: EmbeddingProvider, batch_size: int = 32, debug: bool = False, force_snapshot_id: str = None):
        """
        Genera embeddings per i nodi nel DB.
        Nota: Questo metodo lavora sui dati già nel DB, quindi non richiede worktree locale.
        """
        logger.info(f"🤖 Embedding Start: {provider.model_name}")
        
        # Risolviamo il repository ID
        repo_name = self.repo_url.rstrip('/').split('/')[-1].replace('.git', '')
        repo_id = self.storage.ensure_repository(self.repo_url, self.branch, repo_name)
        
        target_snapshot_id = force_snapshot_id
        if not target_snapshot_id:
             target_snapshot_id = self.storage.get_active_snapshot_id(repo_id)
        
        if not target_snapshot_id:
             raise ValueError("No active snapshot found. Run index() first.")
        
        embedder = CodeEmbedder(self.storage, provider)
        yield from embedder.run_indexing(
            snapshot_id=target_snapshot_id,
            batch_size=batch_size, 
            yield_debug_docs=debug
        )

    def get_stats(self):
        return self.storage.get_stats()