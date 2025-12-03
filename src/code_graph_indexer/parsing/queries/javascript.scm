; === Testing (Jest, Mocha, Jasmine) ===
(call_expression
  function: (identifier) @func_name
  (#match? @func_name "^(describe|context|suite)$")) @role.test_suite

(call_expression
  function: (identifier) @func_name
  (#match? @func_name "^(it|test|specify)$")) @role.test_case

; === Classi ===
(class_declaration) @type.class

; === Funzioni ===
(function_declaration) @type.function
(arrow_function) @type.function
; [FIX] Rinominato function_expression -> function
(function) @type.function

; === API (Express.js style) ===
(call_expression
  function: (member_expression
    property: (property_identifier) @method_name)
  arguments: (arguments (string))
  (#match? @method_name "^(get|post|put|delete|patch|use)$")) @role.api_endpoint