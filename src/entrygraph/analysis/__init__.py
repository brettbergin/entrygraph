"""Query-time intraprocedural taint analysis (#96 Phase 2+).

The graph is call-edge reachability; this package adds a cheap, conservative
same-function reaching-defs check that runs at query time on the candidate
findings only (re-parsing one file per finding), to demote paths where a request
value provably does not reach the sink. It never runs at index time and stores
nothing in the DB.
"""
