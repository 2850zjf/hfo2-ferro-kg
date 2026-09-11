from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.services.literature_discovery import discover_literature
from backend.services.open_access_downloader import download_open_access_pdfs
from backend.services.pipeline_log import record_pipeline_run


GLOBAL_HZO_QUERIES = [
    "ferroelectric HfO2 hafnia",
    "Hf0.5Zr0.5O2 HZO ferroelectric",
    "Hf1-xZrxO2 ferroelectric phase stability",
    "hafnia zirconia ferroelectric oxygen vacancy",
    "hafnia ferroelectric interface electrode TiN",
    "HZO ferroelectric electrode interface endurance retention",
    "hafnia ferroelectric phase stability DFT",
    "machine learning hafnia ferroelectric HfO2",
    "HfO2 ferroelectric phase field simulation",
    "HfO2 ZrO2 ferroelectric superlattice nanolaminate",
    "HfO2 ferroelectric hidden phase transition domain orientation",
    "HfO2 oxygen reservoir electrode ferroelectric",
]


def expand_global_hzo_literature(
    from_date: str = "2011-01-01",
    to_date: str | None = None,
    rows_per_source: int = 75,
    min_score: float = 0.35,
    download_limit: int | None = 80,
    skip_download: bool = False,
    queries: list[str] | None = None,
    output_dir: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    selected_queries = queries or GLOBAL_HZO_QUERIES
    discovery = discover_literature(
        queries=selected_queries,
        from_date=from_date,
        to_date=to_date,
        rows_per_source=rows_per_source,
        min_score=min_score,
        output_dir=output_dir,
        db_path=db_path,
    )
    download = None
    if not skip_download:
        download = download_open_access_pdfs(
            limit=download_limit,
            min_score=min_score,
            db_path=db_path,
        )
    stats = {
        "queries": selected_queries,
        "from_date": from_date,
        "to_date": discovery.get("to_date"),
        "rows_per_source": rows_per_source,
        "min_score": min_score,
        "discovery": discovery,
        "download": download,
    }
    record_pipeline_run("35_expand_global_hzo_literature", "ok", stats, db_path=db_path)
    return stats
