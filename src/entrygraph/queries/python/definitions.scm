; Harvest-level queries: find definition nodes; the Python shaper computes
; qualified names, kinds, docstrings, and decorators by walking from these.

(class_definition
  name: (identifier) @name) @def.class

(function_definition
  name: (identifier) @name) @def.function

(assignment
  left: (identifier) @name) @def.assign
