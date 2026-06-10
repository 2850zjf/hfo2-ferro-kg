from __future__ import annotations

import json
from pathlib import Path


STARTER = Path("computations/mp_hfo2_phase_smoke_test")


def test_mp_phase_targets_are_explicit():
    config = json.loads((STARTER / "phase_targets.json").read_text(encoding="utf-8"))
    labels = {item["label"] for item in config["targets"]}

    assert config["formula"] == "HfO2"
    assert {"monoclinic", "tetragonal", "cubic", "orthorhombic"} <= labels


def test_compute_starter_does_not_commit_generated_or_sensitive_inputs():
    committed_files = [path.name.lower() for path in STARTER.rglob("*") if path.is_file()]

    assert "potcar" not in committed_files
    assert "poscar" not in committed_files
    assert "potcar_not_included.txt" not in committed_files
