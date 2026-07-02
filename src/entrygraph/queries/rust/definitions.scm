; Harvest-level queries: find definition nodes; the Rust shaper computes
; qualified names, kinds, impl-receiver methods, attributes (as decorators),
; and trait conformance by walking from these.

(function_item
  name: (identifier) @name) @def.function

(struct_item
  name: (type_identifier) @name) @def.struct

(enum_item
  name: (type_identifier) @name) @def.enum

(trait_item
  name: (type_identifier) @name) @def.trait

(impl_item) @def.impl

(const_item
  name: (identifier) @name) @def.const

(static_item
  name: (identifier) @name) @def.static

(mod_item
  name: (identifier) @name) @def.mod
