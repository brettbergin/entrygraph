; Harvest-level queries: find definition nodes; the PHP shaper computes
; qualified names (namespace-scoped, `\` -> `.`), kinds, attributes (as
; decorators), and supertypes by walking from these.

(namespace_definition) @namespace

(class_declaration
  name: (name) @name) @def.class

(interface_declaration
  name: (name) @name) @def.interface

(trait_declaration
  name: (name) @name) @def.trait

(function_definition
  name: (name) @name) @def.function

(method_declaration
  name: (name) @name) @def.method

(const_declaration) @def.const

(property_declaration) @def.property
