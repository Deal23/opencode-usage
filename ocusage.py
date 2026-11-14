#!/usr/bin/env python3
"""Compatibility launcher for source checkouts; prefer the installed command."""
from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
    from opencode_usage.core import main

    main()
