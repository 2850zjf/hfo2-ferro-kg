from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.computation_validation import import_computation_results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import user-filled computation result CSV files into the KG computation registry."
    )
    parser.add_argument("results", type=Path)
    parser.add_argument("--job-id", default=None)
    args = parser.parse_args()

    stats = import_computation_results(results_path=args.results, job_id=args.job_id)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
