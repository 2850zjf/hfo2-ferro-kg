from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.table_extractor import extract_tables


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-pdfs", "--limit", type=int, default=None)
    args = parser.parse_args()
    print(extract_tables(limit_pdfs=args.limit_pdfs))


if __name__ == "__main__":
    main()
