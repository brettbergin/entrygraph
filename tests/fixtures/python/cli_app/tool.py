"""argparse tool: parse_args is a catalog cli_arg accessor."""

import argparse
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    args = parser.parse_args()
    subprocess.run(["cat", args.path])


if __name__ == "__main__":
    main()
