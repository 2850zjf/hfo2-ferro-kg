from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.pdf_parser import parse_pending_pdfs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="Reparse already parsed PDFs.")
    args = parser.parse_args()
    print(parse_pending_pdfs(limit=args.limit, force=args.force))


if __name__ == "__main__":
    main()
