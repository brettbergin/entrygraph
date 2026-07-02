"""Enable `python -m entrygraph` alongside the `entrygraph` console script."""

from __future__ import annotations

from entrygraph.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
