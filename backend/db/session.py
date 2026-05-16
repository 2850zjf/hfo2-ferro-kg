from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.core.config import get_settings
from backend.db.init_db import init_database


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    target = Path(db_path) if db_path else get_settings().db_path
    init_database(target)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    return conn
