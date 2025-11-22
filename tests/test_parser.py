import os
import sys
import json
import argparse
import difflib
from typing import List, Dict

# --- FIX IMPORT ---
# Aggiungiamo 'src' al path per importare la libreria locale
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

try:
    from code_graph_indexer.parsing.parser import TreeSitterRepoParser
    from code_graph_indexer.models import ParsingResult
except ImportError as e:
    print(f"[FATAL] Errore importazione: {e}")
    print("Assicurati di essere nella root del progetto.")
    sys.exit(1)

def save_json_debug(data, filepath):
    """Salva il dump JSON formattato."""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"📦 Dati di debug salvati in: {filepath}")

def verify_file_reconstruction(original_path: str, parser_result: ParsingResult):
    """
    Tenta di ricostruire il file originale unendo i chunk.
    Segnala buchi (codice perso) o sovrapposizioni.
    """
    rel_path = os.path.basename(original_path) # O calcola path relativo corretto se necessario
    
    # 1. Filtra i nodi per questo file
    # Nota: il parser usa path relativi, cerchiamo di matchare
    target_nodes = []
    for n in parser_result.nodes:
        # Match euristico sul path (endswith)
        if original_path.endswith(n.file_path):
            target_nodes.append(n)
    
    if not target_nodes:
        print(f"❌ Nessun chunk trovato per il file: {original_path}")
        print("   (Verifica che l'estensione sia supportata e non sia ignorato)")
        return

    # 2. Ordina per byte start
    target_nodes.sort(key=lambda x: x.byte_range[0])

    # 3. Mappa Contenuti
    content_map = {c.chunk_hash: c.content for c in parser_result.contents.values()} if isinstance(parser_result.contents, dict) else {c.chunk_hash: c.content for c in parser_result.contents}

    # 4. Ricostruzione e Analisi Buchi
    reconstructed_content = ""
    current_byte = 0
    gaps = []

    print(f"\n🔍 ANALISI COPERTURA: {original_path}")
    print(f"{'ID Chunk':<10} | {'Range Byte':<15} | {'Tipo':<20} | {'Stato'}")
    print("-" * 70)

    for node in target_nodes:
        start, end = node.byte_range
        chunk_text = content_map.get(node.chunk_hash, "")
        
        status = "OK"
        if start > current_byte:
            gap_size = start - current_byte
            gaps.append((current_byte, start))
            status = f"⚠️  GAP ({gap_size} bytes)"
            # Aggiungiamo un placeholder per il buco nella ricostruzione per visualizzare meglio
            reconstructed_content += f" [MISSING {gap_size} BYTES] "
        elif start < current_byte:
            status = f"❌ OVERLAP ({current_byte - start} bytes)"
        
        print(f"{node.id[:8]}.. | {start:<6} - {end:<6} | {node.type:<20} | {status}")
        
        reconstructed_content += chunk_text
        current_byte = end

    # 5. Confronto con Originale
    try:
        with open(original_path, 'r', encoding='utf-8') as f:
            original_text = f.read()
    except Exception as e:
        print(f"❌ Impossibile leggere file originale: {e}")
        return

    print("-" * 70)
    
    # Normalizziamo per il confronto (rimuovendo i placeholder di gap aggiunti sopra per il check stringa pura)
    # Per un confronto onesto, ricostruiamo pulito:
    reconstructed_clean = "".join([content_map.get(n.chunk_hash, "") for n in target_nodes])
    
    is_identical = (original_text == reconstructed_clean)
    
    if is_identical:
        print("✅ RICOSTRUZIONE PERFETTA: Il file rigenerato è identico byte-per-byte.")
    else:
        print("❌ RICOSTRUZIONE FALLITA: Ci sono differenze.")
        print(f"   Lunghezza Originale:   {len(original_text)}")
        print(f"   Lunghezza Ricostruita: {len(reconstructed_clean)}")
        
        # Mostra le differenze se non è troppo lungo
        if len(original_text) < 5000:
            print("\n--- DIFF ---")
            diff = difflib.ndiff(original_text.splitlines(), reconstructed_clean.splitlines())
            for line in diff:
                if line.startswith(('+', '-', '?')):
                    print(line)
        else:
            print("\n(File troppo grande per mostrare il diff completo)")

    # Export JSON Debug specifico per questo file
    debug_data = {
        "file": original_path,
        "is_perfect_match": is_identical,
        "chunks": [n.to_dict() for n in target_nodes],
        "reconstructed_text": reconstructed_clean
    }
    save_json_debug(debug_data, "debug_reconstruction.json")


def main():
    parser = argparse.ArgumentParser(description="Test Parser & Reconstruction")
    parser.add_argument("input_path", help="Percorso del file o della cartella da testare")
    args = parser.parse_args()

    target_path = os.path.abspath(args.input_path)
    
    # Determina repo_root e file target
    if os.path.isfile(target_path):
        repo_root = os.path.dirname(target_path)
        file_to_check = target_path
    else:
        repo_root = target_path
        file_to_check = None # Controlla tutto o il primo trovato

    print(f"🚀 Avvio Parser su Repo: {repo_root}")
    
    # Esegui Parser
    repo_parser = TreeSitterRepoParser(repo_path=repo_root)
    result = repo_parser.extract_semantic_chunks()
    
    # Export Dati Grezzi
    save_json_debug(result.to_dict(), "debug_parser_full.json")

    # Verifica Ricostruzione
    if file_to_check:
        verify_file_reconstruction(file_to_check, result)
    else:
        # Se è una cartella, verifica il primo file trovato come campione
        if result.files:
            first_file_path = os.path.join(repo_root, result.files[0].path)
            verify_file_reconstruction(first_file_path, result)
        else:
            print("Nessun file trovato da analizzare.")

if __name__ == "__main__":
    main()