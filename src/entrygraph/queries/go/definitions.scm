; Harvest-level queries: find definition nodes; the Go shaper computes
; qualified names, kinds, and receivers by walking from these.

(function_declaration
  name: (identifier) @name) @def.function

(method_declaration
  name: (field_identifier) @name) @def.method

(type_declaration
  (type_spec
    name: (type_identifier) @name
    type: (struct_type)) ) @def.struct

(type_declaration
  (type_spec
    name: (type_identifier) @name
    type: (interface_type)) ) @def.interface

(const_declaration (const_spec name: (identifier) @name)) @def.const

(var_declaration (var_spec name: (identifier) @name)) @def.var

(field_declaration name: (field_identifier) @name) @def.field
