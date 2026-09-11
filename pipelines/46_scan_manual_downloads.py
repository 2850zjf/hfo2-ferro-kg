from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.manual_download_workbench import (
    DEFAULT_CURATION_CSV,
    DEFAULT_INBOX_DIR,
    DEFAULT_WORKBENCH_DIR,
    prepare_manual_download_workbench,
    scan_manual_downloads,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--curation-csv", type=Path, default=DEFAULT_CURATION_CSV)
    parser.add_argument("--workbench-dir", type=Path, default=DEFAULT_WORKBENCH_DIR)
    parser.add_argument("--inbox-dir", type=Path, default=DEFAULT_INBOX_DIR)
    parser.add_argument("--prepare-only", action="store_true", help="Only rebuild the download page/checklists.")
    parser.add_argument("--scan-only", action="store_true", help="Only scan the inbox; skip rebuilding workbench files.")
    parser.add_argument("--copy-to-raw-pdfs", action="store_true", help="Prepare or perform copy into raw_pdfs/manual_downloads.")
    parser.add_argument("--apply", action="store_true", help="Actually copy matched PDFs into the raw corpus.")
    args = parser.parse_args()

    stats: dict[str, object] = {}
    if not args.scan_only:
        stats["workbench"] = prepare_manual_download_workbench(
            curation_csv=args.curation_csv,
            workbench_dir=args.workbench_dir,
            inbox_dir=args.inbox_dir,
        )
    if not args.prepare_only:
        stats["scan"] = scan_manual_downloads(
            curation_csv=args.curation_csv,
            inbox_dir=args.inbox_dir,
            output_dir=args.workbench_dir,
            copy_to_raw_pdfs=args.copy_to_raw_pdfs,
            apply=args.apply,
        )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
