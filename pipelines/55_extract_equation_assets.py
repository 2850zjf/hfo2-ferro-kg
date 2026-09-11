from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.equation_asset_parser import extract_equation_assets


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract page-grounded mathematical equation candidates and crops.")
    parser.add_argument("--limit-pdfs", type=int, default=None)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--no-crops", action="store_true")
    parser.add_argument("--progress-every", type=int, default=20)
    args = parser.parse_args()
    stats = extract_equation_assets(
        limit_pdfs=args.limit_pdfs,
        reset_existing=args.reset,
        save_crops=not args.no_crops,
        progress_every=args.progress_every,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
