from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.config import PROJECT_ROOT, discover_pdf_root
from backend.db.init_db import init_database
from backend.services.analytics import write_markdown_report
from backend.services.chunker import build_chunks
from backend.services.graph_builder import build_graph
from backend.services.hfo2_extractor import run_extraction
from backend.services.pdf_manifest import build_manifest
from backend.services.pdf_parser import parse_pending_pdfs
from backend.services.quality_validator import write_validation_report
from backend.services.table_extractor import extract_tables
from backend.services.vector_store import build_lightweight_index


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-root", type=Path, default=discover_pdf_root())
    parser.add_argument("--limit-pdfs", type=int, default=5)
    parser.add_argument("--limit-chunks", type=int, default=80)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--skip-tables", action="store_true")
    parser.add_argument("--model", default=None)
    parser.add_argument("--force-parse", action="store_true")
    args = parser.parse_args()

    print(f"Project: {PROJECT_ROOT}")
    print(f"PDF root: {args.pdf_root}")
    init_database()
    rows = build_manifest(args.pdf_root)
    print({"manifest_rows": len(rows), "duplicates": sum(row.is_duplicate for row in rows)})
    print(parse_pending_pdfs(limit=args.limit_pdfs, force=args.force_parse))
    if not args.skip_tables:
        print(extract_tables(limit_pdfs=args.limit_pdfs))
    print(build_chunks(limit_pdfs=args.limit_pdfs))
    print(
        run_extraction(
            limit_chunks=args.limit_chunks,
            use_llm=not args.no_llm,
            llm_model=args.model,
        )
    )
    print(build_graph())
    print(build_lightweight_index())
    print({"report": str(write_markdown_report())})
    print({"validation_report": str(write_validation_report())})


if __name__ == "__main__":
    main()
