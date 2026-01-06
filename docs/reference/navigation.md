# Navigation API Reference

The `navigator` module enables **Structural Exploration** of the code. While `Retriever` finds *where* things are, `Navigator` explains *how* they relate.

## CodeNavigator

```python
class CodeNavigator(storage: GraphStorage)
```

Offers an IDE-like traversal API ("Go to Definition", "Find Usages").

### Methods

#### `read_neighbor_chunk`

```python
def read_neighbor_chunk(self, node_id: str, direction: str = "next") -> Optional[Dict[str, Any]]
```

**Description:**
Simulates "Scrolling". Gets the code block immediately preceding or following the current one in the file.

*   **Use Case**: An Agent is reading a function `foo()` and sees a call to `_helper()`. Use `next` to see if `_helper()` is defined right below.

**Arguments:**

*   `direction` (str): `"next"` (down) or `"prev"` (up).

---

#### `read_parent_chunk`

```python
def read_parent_chunk(self, node_id: str) -> Optional[Dict[str, Any]]
```

**Description:**
Jumps up the scope hierarchy.
*   `Method` -> `Class`
*   `Class` -> `Module` (File)

---

#### `analyze_impact` (Incoming)

```python
def analyze_impact(self, node_id: str, limit: int = 20) -> List[Dict[str, Any]]
```

**Description:**
**"Who calls this?"** (Reverse Call Graph).
Identifies all functions or classes that depend on `node_id`. Critical for refactoring agents to know what might break.

**Returns:**
```python
[
    {
        "file": "src/controllers.py",
        "line": 45,
        "relation": "calls",
        "context_snippet": "user.login()"
    }
]
```

---

#### `analyze_dependencies` (Outgoing)

```python
def analyze_dependencies(self, node_id: str) -> List[Dict[str, Any]]
```

**Description:**
**"What does this call?"** (Forward Call Graph).
Lists all external symbols used by `node_id`.

---

#### `visualize_pipeline`

```python
def visualize_pipeline(self, node_id: str, max_depth: int = 2) -> Dict[str, Any]
```

**Description:**
Constructs a recursive tree of the call graph starting from `node_id`.
*   **Use Case**: Generating a UI visualization (Node-Link diagram) for the user to understand complex logic flows.
