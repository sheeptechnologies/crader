import os
import sys
import time
import argparse
import logging
import threading
import statistics
import json
from datetime import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.abspath(os.path.join(current_dir, '..', 'src'))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

try:
    import psutil
except ImportError:
    print("Installa psutil")
    sys.exit(1)

try:
    from code_graph_indexer import CodebaseIndexer
except ImportError as e:
    print(f"Errore import: {e}")
    sys.exit(1)

logging.basicConfig(level=logging.WARNING, format='%(message)s')
logger = logging.getLogger("BENCHMARK")
logger.setLevel(logging.INFO)

def format_bytes(size):
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"

class ResourceMonitor(threading.Thread):
    def __init__(self, interval=0.1):
        super().__init__()
        self.interval = interval
        self.stop_event = threading.Event()
        self.cpu_samples = []
        self.ram_samples = []
        self.process = psutil.Process(os.getpid())

    def run(self):
        self.process.cpu_percent()
        while not self.stop_event.is_set():
            try:
                cpu = self.process.cpu_percent()
                mem = self.process.memory_info().rss
                self.cpu_samples.append(cpu)
                self.ram_samples.append(mem)
                time.sleep(self.interval)
            except Exception: break

    def stop(self):
        self.stop_event.set()
        self.join()

    def get_metrics(self):
        if not self.ram_samples: return {"ram_peak": 0, "cpu_max": 0, "cpu_mean": 0}
        return {
            "ram_peak": max(self.ram_samples),
            "ram_mean": statistics.mean(self.ram_samples),
            "cpu_max": max(self.cpu_samples),
            "cpu_mean": statistics.mean(self.cpu_samples)
        }

def run_benchmark(repo_path: str, output_file: str):
    repo_path = os.path.abspath(repo_path)
    if not os.path.isdir(repo_path): return

    logger.info(f"🚀 AVVIO BENCHMARK SU: {repo_path}")
    
    indexer = CodebaseIndexer(repo_path)
    monitor = ResourceMonitor(interval=0.1)
    
    logger.info("\n--- FASE 1: INDEXING ---")
    start_time = time.time()
    monitor.start()
    
    try:
        indexer.index()
    except Exception as e:
        logger.error(f"Errore: {e}")
        monitor.stop()
        return
    finally:
        monitor.stop()
        end_time = time.time()

    duration = end_time - start_time
    metrics = monitor.get_metrics()
    stats = indexer.get_stats()

    logger.info("\n--- FASE 2: ANALISI TOKEN ---")
    total_chars = 0
    chunk_count = 0
    for content in indexer.get_contents():
        total_chars += len(content['content'])
        chunk_count += 1
    
    est_tokens = total_chars / 4
    
    print("\n" + "="*60)
    print(f"📊 REPORT BENCHMARK")
    print("="*60)
    print(f"\n⏱️  TEMPO:            {duration:.2f} s")
    print(f"💾 RAM Picco:        {format_bytes(metrics['ram_peak'])}")
    print(f"🧠 CPU Media:        {metrics['cpu_mean']:.1f}%")

    print(f"\n📦 DATI GENERATI (DB Stats)")
    # --- FIX CHIAVI QUI SOTTO ---
    print(f"   File Indicizzati: {stats.get('files', 0)}")
    print(f"   Nodi Totali:      {stats.get('total_nodes', 0)}")
    print(f"    -> Chunk Reali:  {stats.get('source_nodes', 0)}")
    print(f"    -> Simboli Ext:  {stats.get('external_nodes', 0)}")
    print(f"   Contenuti Unici:  {stats.get('unique_contents', 0)}")
    print(f"   Archi (Edges):    {stats.get('edges', 0)}")
    
    print(f"\n💰 STIMA TOKEN")
    print(f"   Token Totali:     ~{int(est_tokens):,}")
    print("="*60)

    if output_file:
        result_data = {
            "repo": repo_path, "duration": duration, "resources": metrics, 
            "stats": stats, "tokens": est_tokens
        }
        with open(output_file, "w") as f: json.dump(result_data, f, indent=2)
        logger.info(f"\n[Saved] {output_file}")

    indexer.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_path")
    parser.add_argument("--out", default="benchmark_report.json")
    args = parser.parse_args()
    run_benchmark(args.repo_path, args.out)