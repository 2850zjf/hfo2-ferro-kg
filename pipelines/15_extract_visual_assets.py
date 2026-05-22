from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.visual_asset_parser import extract_visual_assets


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-pdfs", type=int, default=None)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--no-images", action="store_true")
    args = parser.parse_args()
    stats = extract_visual_assets(
        limit_pdfs=args.limit_pdfs,
        reset_existing=args.reset,
        save_images=not args.no_images,
    )
    print(json.dumps(stats, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()

