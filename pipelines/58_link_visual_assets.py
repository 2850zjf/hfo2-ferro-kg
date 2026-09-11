from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.visual_asset_linker import link_visual_assets


def main() -> None:
    parser = argparse.ArgumentParser(description="Link extracted PDF images to nearby figure captions by page geometry.")
    parser.add_argument("--limit-pdfs", type=int, default=None)
    parser.add_argument("--no-reset", action="store_true")
    parser.add_argument("--progress-every", type=int, default=50)
    args = parser.parse_args()
    stats = link_visual_assets(
        limit_pdfs=args.limit_pdfs,
        reset_existing=not args.no_reset,
        progress_every=args.progress_every,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
