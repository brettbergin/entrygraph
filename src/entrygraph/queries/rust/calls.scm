; Both call_expression AND macro_invocation must be captured: without macros,
; sqlx::query! and route macros are invisible to the sink/entrypoint layer.

(call_expression) @call
(macro_invocation) @macro
