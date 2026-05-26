from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.rag_job_service import run_rag_job


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one persisted RAG question job.")
    parser.add_argument("job_id")
    args = parser.parse_args()
    job = run_rag_job(args.job_id)
    print(f"{job.get('job_id')} {job.get('status')}")


if __name__ == "__main__":
    main()
