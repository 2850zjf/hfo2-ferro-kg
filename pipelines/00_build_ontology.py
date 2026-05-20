from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.db.init_db import init_database
from backend.services.ontology_builder import build_ontology


if __name__ == "__main__":
    init_database()
    print(json.dumps(build_ontology(), ensure_ascii=False, indent=2))
