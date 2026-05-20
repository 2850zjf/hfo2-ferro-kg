from __future__ import annotations

import json

from backend.db.init_db import init_database, list_tables
from backend.services.literature_discovery import discover_literature


def test_discover_literature_with_mocked_sources(tmp_path):
    db_path = tmp_path / "hfo2.sqlite3"
    output_dir = tmp_path / "literature"
    init_database(db_path)

    def fake_fetch(url: str):
        if "openalex" in url:
            return {
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "doi": "https://doi.org/10.1234/hzo.2026.1",
                        "title": "Ferroelectric HfO2 thin films with orthorhombic phase",
                        "publication_year": 2026,
                        "authorships": [{"raw_author_name": "A. Researcher"}],
                        "primary_location": {
                            "source": {"display_name": "Journal of Hafnia", "publisher": "Example"},
                            "pdf_url": "https://example.org/open.pdf",
                        },
                        "open_access": {"oa_status": "gold", "oa_url": "https://example.org/article"},
                        "abstract_inverted_index": {
                            "HfO2": [0],
                            "ferroelectric": [1],
                            "Pca21": [2],
                            "phase": [3],
                        },
                    }
                ]
            }
        return {
            "message": {
                "items": [
                    {
                        "DOI": "10.1234/hzo.2026.1",
                        "title": ["Duplicate Crossref HfO2 ferroelectric article"],
                        "container-title": ["Journal of Hafnia"],
                        "publisher": "Example",
                        "published-online": {"date-parts": [[2026, 1, 1]]},
                    }
                ]
            }
        }

    stats = discover_literature(
        queries=["HfO2 ferroelectric"],
        rows_per_source=1,
        fetch_json=fake_fetch,
        output_dir=output_dir,
        db_path=db_path,
    )

    assert stats["candidates"] == 1
    assert stats["with_doi"] == 1
    assert stats["with_oa_url"] == 1
    assert "literature_candidates" in list_tables(db_path)
    rows = [json.loads(line) for line in (output_dir / "literature_candidates.jsonl").read_text().splitlines()]
    assert rows[0]["doi"] == "10.1234/hzo.2026.1"
