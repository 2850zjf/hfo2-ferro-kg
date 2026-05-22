from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.multi_model_validator import run_multi_model_validation


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--include-llm", action="store_true")
    parser.add_argument("--models", default=None, help="Comma-separated LLM model names.")
    args = parser.parse_args()
    models = [item.strip() for item in args.models.split(",") if item.strip()] if args.models else None
    stats = run_multi_model_validation(limit=args.limit, include_llm=args.include_llm, llm_models=models)
    print(json.dumps(stats, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
