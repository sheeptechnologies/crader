; ============================================================
; 1. SPECIAL ROLES (Priorità Semantica)
; ============================================================

; Entry Point
(if_statement
  condition: (comparison_operator
    (identifier) @name
    (string) @val)
  (#eq? @name "__name__")
  (#match? @val "__main__")) @role.entry_point

; Test Suite
(class_definition
  superclasses: (argument_list
    (identifier) @parent)
  (#match? @parent "(TestCase|.*Test)$")) @role.test_suite

; Test Case
(function_definition
  name: (identifier) @name
  (#match? @name "^test_")) @role.test_case

; API Endpoints (Flask/FastAPI)
(decorated_definition
  (decorator
    (call
      function: (attribute
        attribute: (identifier) @attr)))
  (#match? @attr "^(get|post|put|delete|patch|route)$")) @role.api_endpoint

; Data Models (Dataclass)
(decorated_definition
  (decorator
    (identifier) @dec)
  (#eq? @dec "dataclass")) @role.data_schema

; Data Models (Pydantic)
(class_definition
  superclasses: (argument_list
    (identifier) @parent)
  (#eq? @parent "BaseModel")) @role.data_schema


; ============================================================
; 2. BASE TYPES (Fallback Strutturali - ANCORATI AI NOMI)
; ============================================================

; Cattura solo l'identificatore della classe, non tutto il corpo
(class_definition
  name: (identifier) @type.class)

; Cattura solo l'identificatore della funzione
(function_definition
  name: (identifier) @type.function)