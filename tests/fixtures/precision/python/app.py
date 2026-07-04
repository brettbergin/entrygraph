"""Constructs that MUST NOT be tagged, with positive controls (#97)."""

import hashlib
import subprocess


def safe(token: str) -> None:
    hashlib.sha256(token.encode()).hexdigest()  # NOT weak_crypto
    subprocess.run(["ls", "-l"])  # command_exec today; noted in #97 audit


def dangerous(name: str) -> None:
    hashlib.md5(name.encode())  # weak_crypto (control)
