"""FastAPI app exercising param-level taint sources (#134).

Each handler declares an attacker-controlled request input via a FastAPI
declarator (`Query`/`Path`/`Body`/`Header`) and flows it into a command sink,
so the finding should identify the channel — not just "this handler is shaped
like a source."
"""

import subprocess

from fastapi import Body, FastAPI, Header, Path, Query

app = FastAPI()


@app.get("/search")
def search(q: str = Query(...)):
    return subprocess.run(q, shell=True)


@app.get("/items/{item_id}")
def read_item(item_id: str = Path(...)):
    return subprocess.run(item_id, shell=True)


@app.post("/exec")
def do_exec(payload: str = Body(...)):
    return subprocess.check_output(payload, shell=True)


@app.get("/agent")
def agent(user_agent: str = Header(...)):
    return subprocess.run(user_agent, shell=True)


@app.get("/multiline")
def multiline(
    token: str = Query(
        default="",
    ),
):
    # multi-line signature: the declarator sits below the def line, so detecting
    # it needs the "before first body statement" rule, not a def-line match
    return subprocess.run(token, shell=True)


@app.get("/run/{cmd}")
def run_typed(cmd: str):
    # A bare typed path param with no declarator: only handler-as-source can see
    # it, so the resulting path carries no channel.
    return subprocess.run(cmd, shell=True)
