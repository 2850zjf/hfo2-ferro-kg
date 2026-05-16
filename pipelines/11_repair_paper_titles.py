from __future__ import annotations

import sys
from pathlib import Path
import argparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.paper_title_repair import repair_paper_titles


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--use-doi", action="store_true", help="Use DOI metadata to repair titles when available.")
    args = parser.parse_args()
    print(repair_paper_titles(use_doi=args.use_doi))
