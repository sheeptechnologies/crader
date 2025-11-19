import sys, os, argparse, time, logging
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))
from code_graph_indexer import CodebaseIndexer

logging.basicConfig(level=logging.INFO)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    args = parser.parse_args()
    
    print(f"Indexing {args.path}...")
    idx = CodebaseIndexer(args.path)
    t0 = time.time()
    builder = idx.index_optimized(lambda f, c: print(".", end="", flush=True))
    print(f"\nDone in {time.time()-t0:.2f}s")
    
    stats = builder.get_stats()
    print(f"Nodes: {stats['nodes']}, Edges: {stats['edges']}")
    builder.export_json("debug_graph.json")
    print("Graph saved to debug_graph.json")

if __name__ == "__main__": main()