from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.open_access_downloader import download_open_access_pdfs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--min-score", type=float, default=0.45)
    args = parser.parse_args()
    stats = download_open_access_pdfs(limit=args.limit, min_score=args.min_score)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
