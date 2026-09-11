from __future__ import annotations

from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROTECTED_RUNTIME_DIRS = (
    PROJECT_ROOT / "data" / "exports",
    PROJECT_ROOT / "data" / "extraction_candidates",
    PROJECT_ROOT / "data" / "ontology",
)


def _runtime_fingerprint() -> dict[str, tuple[int, int]]:
    """Capture enough metadata to detect tests mutating production-style outputs."""

    result: dict[str, tuple[int, int]] = {}
    for root in PROTECTED_RUNTIME_DIRS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                result[str(path.relative_to(PROJECT_ROOT))] = (
                    path.stat().st_size,
                    path.stat().st_mtime_ns,
                )
    return result


@pytest.fixture(scope="session", autouse=True)
def prevent_production_output_pollution():
    """Fail the suite if a test writes to project data output directories."""

    before = _runtime_fingerprint()
    yield
    after = _runtime_fingerprint()
    assert after == before, (
        "pytest modified protected project data outputs; pass tmp_path/output_dir "
        "to the service under test"
    )
