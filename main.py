#!/usr/bin/env python3
"""
main.py — Perchance App Engine entry point.
Run this to open the launcher.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.resolve()))

from launcher.launcher import main

if __name__ == "__main__":
    main()