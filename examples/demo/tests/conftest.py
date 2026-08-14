"""Make `demo` importable from src/ regardless of pytest rootdir."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
