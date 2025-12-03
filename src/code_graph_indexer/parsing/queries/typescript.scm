; === Testing ===
(call_expression
  function: (identifier) @func_name
  (#match? @func_name "^(describe|context|suite)$")) @role.test_suite

(call_expression
  function: (identifier) @func_name
  (#match? @func_name "^(it|test)$")) @role.test_case

; === Strutture Dati ===
(interface_declaration) @role.data_schema
(type_alias_declaration) @role.data_schema

; === Classi & Funzioni ===
(class_declaration) @type.class
(function_declaration) @type.function
(arrow_function) @type.function
; [FIX] Rinominato function_expression -> function
(function) @type.function

; === Angular / NestJS Decorators ===
(decorator
  (call_expression
    function: (identifier) @dec_name
    (#eq? @dec_name "Controller"))) @role.api_endpoint

(decorator
  (call_expression
    function: (identifier) @dec_name
    (#match? @dec_name "^(Get|Post|Put|Delete|Patch)$"))) @role.api_endpoint