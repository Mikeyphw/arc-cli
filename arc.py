#!/usr/bin/env python3
"""Development launcher for arc-cli without installing the package."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from arc_cli.cli import main

raise SystemExit(main())
