from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.config import discover_pdf_root
from backend.services.analytics import write_markdown_report
from backend.services.chunker import build_chunks
from backend.services.graph_builder import build_graph
from backend.services.hfo2_extractor import run_extraction
from backend.services.literature_discovery import DEFAULT_QUERIES, discover_literature
from backend.services.open_access_downloader import download_open_access_pdfs
from backend.services.pdf_manifest import build_manifest
from backend.services.pdf_parser import parse_pending_pdfs
from backend.services.quality_validator import write_validation_report
from backend.services.table_extractor import extract_tables
from backend.services.vector_store import build_lightweight_index


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", action="append")
    parser.add_argument("--from-date", default="2024-01-01")
    parser.add_argument("--to-date", default=None)
    parser.add_argument("--rows-per-source", type=int, default=25)
    parser.add_argument("--min-score", type=float, default=0.45)
    parser.add_argument("--download-limit", type=int, default=10)
    parser.add_argument("--skip-discovery", action="store_true")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args()

    stats: dict[str, object] = {}
    if not args.skip_discovery:
        stats["discover"] = discover_literature(
            queries=args.query or DEFAULT_QUERIES,
            from_date=args.from_date,
            to_date=args.to_date,
            rows_per_source=args.rows_per_source,
            min_score=args.min_score,
        )
    if not args.skip_download:
        stats["download"] = download_open_access_pdfs(
            limit=args.download_limit,
            min_score=args.min_score,
        )

    pdf_root = discover_pdf_root()
    rows = build_manifest(pdf_root)
    stats["manifest"] = {
        "pdf_root": str(pdf_root),
        "manifest_rows": len(rows),
        "duplicates": sum(row.is_duplicate for row in rows),
    }
    stats["parse"] = parse_pending_pdfs()
    stats["tables"] = extract_tables(incremental=True)
    stats["chunks"] = build_chunks(reset_existing=False)
    stats["extraction"] = run_extraction(
        use_llm=not args.no_llm,
        reset_existing=False,
    )
    stats["graph"] = build_graph()
    stats["index"] = build_lightweight_index()
    stats["report"] = str(write_markdown_report())
    stats["validation_report"] = str(write_validation_report())
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
