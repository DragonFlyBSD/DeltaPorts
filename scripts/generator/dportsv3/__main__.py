"""Entry point for `python -m dportsv3`."""

from __future__ import annotations

import sys

from dportsv3.cli import main


if __name__ == "__main__":
    sys.exit(main())
