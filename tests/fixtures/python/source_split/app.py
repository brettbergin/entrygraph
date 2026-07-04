"""Explicit-read handler vs handler-as-source, both reaching os.system (#96 P1)."""

import os

from flask import Flask, request

app = Flask(__name__)


@app.route("/explicit")
def explicit_handler():
    # demonstrable request read -> explicit source
    name = request.args.get("name")
    os.system("echo " + name)


@app.route("/implicit")
def implicit_handler():
    # no request read at all; reaches the same sink via a constant — handler-as-source
    return helper()


def helper():
    os.system("ls")
