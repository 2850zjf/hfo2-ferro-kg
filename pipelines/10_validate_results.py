from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.quality_validator import validate_local_results, write_validation_report


if __name__ == "__main__":
    report_path = write_validation_report()
    summary = validate_local_results()
    print(f"Validation report: {report_path}")
    print(json.dumps(summary["quality_gate"], ensure_ascii=False, indent=2))
