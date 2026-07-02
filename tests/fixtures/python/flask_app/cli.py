"""CLI entrypoint for the fixture app."""

import click
from app.services import run_report


@click.command()
@click.argument("name")
def report(name):
    """Generate a report from the command line."""
    run_report(name)


if __name__ == "__main__":
    report()
