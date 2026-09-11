from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.extraction_quality_gate import apply_excluded_candidate_gate


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove publication-excluded chunks from one ontology candidate layer.")
    parser.add_argument("--excluded-csv", type=Path, default=None)
    parser.add_argument("--ontology-version", default="hfo2-ferrokg-v2.3")
    parser.add_argument("--apply", action="store_true", help="Apply deletion; default is a read-only preview.")
    args = parser.parse_args()
    result = apply_excluded_candidate_gate(
        excluded_csv=args.excluded_csv,
        ontology_version=args.ontology_version,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
