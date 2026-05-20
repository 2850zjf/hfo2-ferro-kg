from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.chunker import build_chunks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-pdfs", type=int, default=None)
    parser.add_argument("--incremental", action="store_true", help="Only chunk parsed PDFs with no existing chunks.")
    args = parser.parse_args()
    print(build_chunks(limit_pdfs=args.limit_pdfs, reset_existing=not args.incremental))


if __name__ == "__main__":
    main()
