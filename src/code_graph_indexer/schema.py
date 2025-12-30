from typing import Literal

# ==========================================
# VOCABOLARIO SEMANTICO (Costanti)
# ==========================================

# Ruoli funzionali del codice (derivati da analisi semantica .scm)
# SEMANTIC VOCABULARY (Constants)
# ==========================================

# Functional roles of the code (derived from semantic analysis .scm)
# These values must EXACTLY match the tags used in queries/*.scm files
# and the fallbacks in parser.py.
VALID_ROLES = Literal[
    "entry_point",    # Es: if __name__ == "__main__", func main()
    "api_endpoint",   # Es: @app.get, @router.post
    "test_case",      # Es: def test_..., @test
    "test_suite",     # Es: class Test..., describe()
    "data_schema",    # Es: @dataclass, Pydantic BaseModel, struct
    "class",          # Definizione generica di classe (fallback)
    "function",       # Definizione generica di funzione (fallback)
    "method",         # Metodo di classe (fallback)
    "module"          # Scope del file/modulo
]

# Macroscopic categories
# 1. Derived from File System (MetadataProvider): test, config, docs, code
# 2. Derived from Semantics (Parser): logic, definition
VALID_CATEGORIES = Literal[
    "test",           # File di test o chunk di test
    "config",         # File .env/.json o costanti di configurazione
    "docs",           # File markdown/txt o blocchi di documentazione
    "code",           # Codice sorgente generico (default per file)
    "logic",          # Algoritmi e flussi di controllo (default per chunk)
    "definition"      # Definizioni di tipi, interfacce, schemi
]