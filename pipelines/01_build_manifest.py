from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.config import PROJECT_ROOT, discover_pdf_root
from backend.services.pdf_manifest import build_manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-root", type=Path, default=discover_pdf_root())
    args = parser.parse_args()
    rows = build_manifest(args.pdf_root, PROJECT_ROOT / "data" / "manifest")
    duplicates = sum(1 for row in rows if row.is_duplicate)
    print(f"PDF root: {args.pdf_root}")
    print(f"Manifest rows: {len(rows)}")
    print(f"Duplicates: {duplicates}")


if __name__ == "__main__":
    main()
