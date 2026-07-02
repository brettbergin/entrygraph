import shlex
import subprocess


def sanitized(cmd):
    # shlex.quote is a registered command_exec sanitizer, called on the same
    # function as the sink (a sibling call, not a node on the source->sink path).
    subprocess.run(shlex.quote(cmd))


def unsanitized(cmd):
    subprocess.run(cmd)
