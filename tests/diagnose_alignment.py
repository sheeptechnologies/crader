import os
import sys
import logging

# --- SETUP PATH ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from code_graph_indexer.parsing.parser import TreeSitterRepoParser
from code_graph_indexer.graph.indexers.scip import SCIPIndexer

# Configura logging pulito
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("DIAGNOSTIC")

def diagnose(repo_path: str):
    repo_path = os.path.abspath(repo_path)
    logger.info(f"🔍 DIAGNOSTICA SU: {repo_path}")

    # 1. ESTRAZIONE CHUNK (Tree-sitter)
    logger.info("\n[1] Esecuzione Tree-sitter...")
    parser = TreeSitterRepoParser(repo_path)
    parsing_result = parser.extract_semantic_chunks()
    
    # Mappa: file_path -> List[ChunkNode]
    ts_map = {}
    for node in parsing_result.nodes:
        if node.file_path not in ts_map:
            ts_map[node.file_path] = []
        ts_map[node.file_path].append(node)
        
    logger.info(f"    -> Trovati {len(ts_map)} file con chunk.")
    if not ts_map:
        logger.error("❌ Tree-sitter non ha trovato nulla. Controlla il path.")
        return

    # 2. ESTRAZIONE RELAZIONI (SCIP)
    logger.info("\n[2] Esecuzione SCIP...")
    scip = SCIPIndexer(repo_path)
    relations = scip.extract_relations({}) # Passiamo dict vuoto
    
    # Mappa: file_path -> List[Relation]
    scip_map = {}
    for rel in relations:
        if rel.source_file not in scip_map:
            scip_map[rel.source_file] = []
        scip_map[rel.source_file].append(rel)
        
    logger.info(f"    -> Trovati {len(scip_map)} file con relazioni in uscita.")

    # 3. ANALISI DISALLINEAMENTO PATH
    logger.info("\n[3] Analisi Path Mismatch...")
    
    ts_files = set(ts_map.keys())
    scip_files = set(scip_map.keys())
    common_files = ts_files.intersection(scip_files)
    
    if not common_files:
        logger.error("❌ NESSUN FILE IN COMUNE TRA I DUE TOOL!")
        logger.info(f"    Esempio Tree-sitter: {list(ts_files)[:3]}")
        logger.info(f"    Esempio SCIP:        {list(scip_files)[:3]}")
        logger.info("    SUGGERIMENTO: Il problema è nella normalizzazione dei percorsi in scip.py.")
        return
    else:
        logger.info(f"✅ {len(common_files)} file combaciano. I path sembrano corretti.")

    # 4. ANALISI MATEMATICA RANGE (Sul primo file comune)
    sample_file = list(common_files)[0]
    logger.info(f"\n[4] Deep Dive sul file: '{sample_file}'")
    
    chunks = sorted(ts_map[sample_file], key=lambda c: c.byte_range[0])
    rels = scip_map[sample_file]
    
    logger.info(f"    Chunk trovati: {len(chunks)}")
    logger.info(f"    Relazioni da mappare: {len(rels)}")
    
    matches = 0
    failures = 0
    
    print("\n    --- TEST MATCHING (Primi 5 tentativi) ---")
    for i, rel in enumerate(rels[:5]):
        r_start, r_end = rel.source_byte_range
        found = False
        
        # Simuliamo la logica del Builder
        for chunk in chunks:
            c_start, c_end = chunk.byte_range
            
            # Logica esatta del Builder
            if c_start <= r_start and c_end >= r_end:
                print(f"    ✅ MATCH: Rel [{r_start}-{r_end}] è dentro Chunk [{c_start}-{c_end}] ({chunk.type})")
                found = True
                matches += 1
                break
        
        if not found:
            print(f"    ❌ FAIL: Rel [{r_start}-{r_end}] non entra in nessun chunk.")
            # Cerchiamo il chunk più vicino per capire perché
            closest = min(chunks, key=lambda c: min(abs(c.byte_range[0] - r_start), abs(c.byte_range[1] - r_end)))
            print(f"       Chunk più vicino: [{closest.byte_range[0]}-{closest.byte_range[1]}] ({closest.type})")
            failures += 1

    logger.info(f"\n[5] Risultato Diagnosi:")
    if failures > 0:
        logger.warning("⚠️  I path combaciano, ma i byte range sono disallineati.")
        logger.warning("    Possibili cause: encoding diverso (CRLF vs LF), file modificati, o chunking troppo aggressivo.")
    else:
        logger.info("🚀 Tutto sembra corretto sui campioni analizzati!")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_path")
    args = parser.parse_args()
    diagnose(args.repo_path)