from crader.models import Repository, RetrievedContext


def test_repository_to_dict():
    repo = Repository(id="r1", url="u", name="n", branch="b")
    data = repo.to_dict()
    assert data["id"] == "r1"
    assert data["current_snapshot_id"] is None


def test_retrieved_context_render_includes_navigation():
    ctx = RetrievedContext(
        node_id="n1",
        file_path="src/app.py",
        content="print('hi')",
        semantic_labels=["Function"],
        start_line=1,
        end_line=2,
        language="python",
        nav_hints={
            "parent": {"label": "Class", "id": "p1"},
            "prev": {"label": "Prev", "id": "p2"},
            "next": {"label": "Next", "id": "p3"},
        },
        outgoing_definitions=["foo", "bar"],
    )

    rendered = ctx.render()
    assert "FILE: src/app.py" in rendered
    assert "[Function]" in rendered
    assert "RELATIONS:" in rendered
    assert "SEMANTIC_PARENT_CHUNK" in rendered
