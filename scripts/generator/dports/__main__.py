"""
DPorts v2 entry point for `python -m dports`.

Usage:
    python -m dports <command> [options]
    python -m dports merge --target 2025Q1 category/port
    python -m dports check category/port
"""

import sys
from dports.cli import main

if __name__ == "__main__":
    sys.exit(main())
