"""Root conftest — ensures project root is on sys.path for pipeline imports."""

import sys
from pathlib import Path

# Add project root so 'pipeline' package is importable
project_root = str(Path(__file__).resolve().parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
