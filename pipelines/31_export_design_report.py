from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.design_report import export_design_progress_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Export HfO2 design workflow progress report.")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    stats = export_design_progress_report(output_path=args.output)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
