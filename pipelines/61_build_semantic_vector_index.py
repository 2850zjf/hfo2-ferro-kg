from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.semantic_vector_store import (
    DEFAULT_DIMENSIONS,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_SOURCE_TYPES,
    build_semantic_index,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the resumable Qwen semantic evidence index.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--dimensions", type=int, default=DEFAULT_DIMENSIONS)
    parser.add_argument("--source-types", nargs="+", default=list(DEFAULT_SOURCE_TYPES))
    args = parser.parse_args()
    stats = build_semantic_index(
        limit=args.limit,
        batch_size=args.batch_size,
        model=args.model,
        dimensions=args.dimensions,
        source_types=args.source_types,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
