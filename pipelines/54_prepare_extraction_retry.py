from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.extraction_quality_gate import reset_retryable_candidates


def main() -> None:
    parser = argparse.ArgumentParser(description="Clear extraction-error placeholders so a scoped queue can retry them.")
    parser.add_argument("--chunk-list", type=Path, required=True)
    parser.add_argument("--ontology-version", default="hfo2-ferrokg-v2.3")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = reset_retryable_candidates(
        chunk_list=args.chunk_list,
        ontology_version=args.ontology_version,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
