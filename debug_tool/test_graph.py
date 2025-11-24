import urllib.request
import json
import re
import sys

BASE_URL = "http://localhost:8001/api"

def get_json(url):
    with urllib.request.urlopen(url) as response:
        return json.loads(response.read().decode())

def test_graph():
    try:
        # 1. Get files
        print("Fetching files...")
        data = get_json(f"{BASE_URL}/files")
        files = data['files']
        if not files:
            print("No files found.")
            return

        first_file = files[0]['path']
        print(f"Checking file: {first_file}")
        
        # 2. Get file view
        data = get_json(f"{BASE_URL}/file_view?path={first_file}")
        html_content = data['html']
        
        # 3. Find chunk ID
        match = re.search(r'data-id="([^"]+)"', html_content)
        if match:
            chunk_id = match.group(1)
            print(f"Found chunk ID: {chunk_id}")
            
            # 4. Get graph
            print(f"Fetching graph for {chunk_id}...")
            graph = get_json(f"{BASE_URL}/chunk/{chunk_id}/graph")
            print(f"Graph nodes: {len(graph['nodes'])}")
            print(f"Graph edges: {len(graph['edges'])}")
        else:
            print("No chunks found in file.")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_graph()
