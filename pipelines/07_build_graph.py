from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.graph_builder import build_graph


if __name__ == "__main__":
    print(build_graph())
