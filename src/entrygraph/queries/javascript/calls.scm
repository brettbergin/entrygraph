(call_expression) @call
(member_expression) @member
(subscript_expression) @subscript
; heritage is harvested in the shaper's _heritage(); a `class_heritage
; (identifier)` pattern is impossible in the TS grammar (clauses wrap the
; identifiers there) and would fail to compile the shared query for TypeScript.
