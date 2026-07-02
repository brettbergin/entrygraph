; Harvest-level queries: find definition nodes; the Ruby shaper computes
; qualified names, kinds, and scope chains by walking from these.

(class
  name: (constant) @name) @def.class

(module
  name: (constant) @name) @def.module

(method
  name: (identifier) @name) @def.method

(singleton_method
  name: (identifier) @name) @def.singleton_method

(assignment
  left: (constant) @name) @def.assign.constant

(assignment
  left: (identifier) @name) @def.assign.variable
