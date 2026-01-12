from crader import schema


def test_schema_imports():
    # Just verify that the module can be imported and constants are defined
    assert hasattr(schema, "VALID_ROLES")
    assert hasattr(schema, "VALID_CATEGORIES")
