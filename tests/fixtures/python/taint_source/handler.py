import os


def handler():
    # os.getenv is a registered taint source (category "env_input").
    cmd = os.getenv("CMD")
    run(cmd)


def run(cmd):
    # os.system is a registered command_exec sink.
    os.system(cmd)
