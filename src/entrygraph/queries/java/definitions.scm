; Harvest-level queries: find definition nodes; the Java shaper computes
; qualified names, kinds, annotations (as decorators), and supertypes by
; walking from these.

(class_declaration
  name: (identifier) @name) @def.class

(interface_declaration
  name: (identifier) @name) @def.interface

(method_declaration
  name: (identifier) @name) @def.method

(field_declaration) @def.field
