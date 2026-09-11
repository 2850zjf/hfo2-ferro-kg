from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.computation_validation import import_computation_results


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import a user-filled computation CSV as unverified metadata. "
            "This command never certifies a row as raw-output verified."
        )
    )
    parser.add_argument("results", type=Path)
    parser.add_argument("--job-id", default=None)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for normalized CSV and report; defaults to the input CSV directory.",
    )
    args = parser.parse_args()

    stats = import_computation_results(
        results_path=args.results,
        job_id=args.job_id,
        output_dir=args.output_dir,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
