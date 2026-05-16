from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.analytics import write_markdown_report


if __name__ == "__main__":
    print(write_markdown_report())
