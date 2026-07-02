; Ruby "imports" are method calls: require / require_relative / load. The
; shaper filters calls by method name; capturing all calls keeps this cheap
; and avoids brittle string predicates.

(call
  method: (identifier) @import.method
  arguments: (argument_list)) @import.call
