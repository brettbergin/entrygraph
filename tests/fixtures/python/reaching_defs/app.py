"""Same-function reaching check fixtures (#96 Phase 2)."""

import os

from flask import Flask, request

app = Flask(__name__)


@app.route("/confirmed")
def confirmed_handler():
    # request value flows into the sink argument
    name = request.args.get("name")
    os.system("echo " + name)


@app.route("/refuted")
def refuted_handler():
    # reads request, but the sink runs an unrelated constant
    _ignored = request.args.get("name")
    os.system("ls -l")
