"""In-process background jobs: a DB-backed queue with an asyncio runner.

No Redis or external broker — the Job table is the queue, claims are atomic
UPDATEs, and the runner lives in the server's lifespan. Sentinel's arq path is
separate and unaffected.
"""
