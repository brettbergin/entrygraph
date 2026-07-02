(import_statement) @import
(call_expression
  function: (identifier) @req (#eq? @req "require")
  arguments: (arguments (string) @req.module)) @require
; re-exports (barrel files): `export { X } from "./y"` / `export * from "./y"`
(export_statement source: (string)) @export.from
