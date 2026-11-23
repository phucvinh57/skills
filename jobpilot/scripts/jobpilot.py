#!/usr/bin/env python3
"""JobPilot skill: the one entry point. `python jobpilot.py <group> <command> ...` prints JSON."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jp.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
