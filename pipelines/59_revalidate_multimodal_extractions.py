from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.multimodal_extractor import revalidate_multimodal_extractions


def main() -> None:
    parser = argparse.ArgumentParser(description="Reapply current ontology and deterministic quality gates to multimodal outputs.")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    print(json.dumps(revalidate_multimodal_extractions(limit=args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
