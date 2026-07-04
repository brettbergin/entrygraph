"""Click CLI whose command handler flows an argument into command execution."""

import subprocess

import click


@click.command()
@click.argument("target")
def deploy(target):
    run_deploy(target)


def run_deploy(target):
    subprocess.run("deploy.sh " + target, shell=True)


if __name__ == "__main__":
    deploy()
