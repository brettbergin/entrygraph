; Harvest-level queries: find definition nodes; the C# shaper computes
; qualified names (from the enclosing namespace), kinds, attributes (as
; decorators), modifiers, and supertypes by walking from these.

(class_declaration
  name: (identifier) @name) @def.class

(interface_declaration
  name: (identifier) @name) @def.interface

(struct_declaration
  name: (identifier) @name) @def.struct

(record_declaration
  name: (identifier) @name) @def.record

(method_declaration
  name: (identifier) @name) @def.method

(constructor_declaration
  name: (identifier) @name) @def.constructor

(local_function_statement
  name: (identifier) @name) @def.local_function

(property_declaration
  name: (identifier) @name) @def.property

(field_declaration) @def.field
