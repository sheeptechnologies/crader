# Navigation API

`CodeNavigator` exposes helpers for traversing the code graph.

## CodeNavigator

```python
from crader import CodeNavigator

nav = CodeNavigator(storage)
```

### read_neighbor_chunk

```python
next_chunk = nav.read_neighbor_chunk(node_id, direction="next")
prev_chunk = nav.read_neighbor_chunk(node_id, direction="prev")
```

Returns the adjacent chunk in the same file (if any).

### read_parent_chunk

```python
parent = nav.read_parent_chunk(node_id)
```

Returns the enclosing chunk (for example, a class containing a method).

### analyze_impact

```python
callers = nav.analyze_impact(node_id, limit=20)
```

Returns a list of incoming references (`calls`, `references`, `imports`, `instantiates`).

### analyze_dependencies

```python
deps = nav.analyze_dependencies(node_id)
```

Returns outgoing calls for the node. If none are available, the method returns `None`.

### visualize_pipeline

```python
flow = nav.visualize_pipeline(node_id, max_depth=2)
```

Builds a recursive call tree for UI visualization.
