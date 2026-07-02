"""HTTP routes for the fixture app."""

from flask import Flask, request

from app.services import lookup_user, run_report

app = Flask(__name__)


@app.route("/users/<user_id>")
def get_user(user_id):
    """Fetch a user by id."""
    return lookup_user(user_id)


@app.route("/reports", methods=["GET", "POST"])
def create_report():
    """Kick off a report; reaches subprocess through the service layer."""
    name = request.args.get("name", "default")
    return run_report(name)


@app.route("/health")
def health():
    return {"ok": True}
