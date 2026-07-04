"""Two handlers whose sources differ only by request channel (#87)."""

import subprocess

from flask import Flask, request

app = Flask(__name__)


@app.route("/run")
def run_query():
    q = request.args.get("q")
    return subprocess.run(q)


@app.route("/hdr")
def run_header():
    key = request.headers.get("X-Api-Key")
    return subprocess.run(key)
