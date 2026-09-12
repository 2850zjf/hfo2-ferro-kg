"""Shared pytest configuration for HfO2-FerroKG.

Purpose
-------
Fail the whole suite if any test touches the production runtime output tree.

Several services compute their output location from ``PROJECT_ROOT`` at call
time -- for example ``multi_model_validator`` writes ``PROJECT_ROOT/data/exports``,
``hfo2_extractor`` writes ``PROJECT_ROOT/data/extraction_candidates``, and
``ontology_builder.load_ontology_bundle`` writes
``PROJECT_ROOT/data/ontology/<version>``. A test that calls those without
passing an explicit output directory therefore writes into the real data tree
instead of ``tmp_path``. That is invisible in a normal run: the files already
exist, the byte counts barely move, and the suite still reports success.

This fixture fingerprints the protected directories before and after the
session and fails on any difference, so the defect surfaces instead of silently
overwriting research artefacts.

Detection is by ``(size, mtime_ns)`` rather than by content hash, for two
reasons:

  * hashing ``data/computation`` (~154 MB, ~3.5k files) twice per session added
    ~140 s to the main repository's run. A guard that makes the suite painful to
    run gets bypassed, which is worse than the defect it catches.
  * a test rewriting a production file with *identical bytes* is still a test
    writing into the production tree. Content hashing would excuse it; size and
    mtime do not.

What this cannot protect
------------------------
The production SQLite database. Tests must not point ``HFO2_FERROKG_DB_PATH`` at
it. In the main repository that default path IS the ~1 GB production database,
and ``backend/db/session.py:connect()`` opens it read-write and calls
``init_database()``. Use ``scripts/migration/run_main_repo_pytest.sh``, which
redirects the database to a scratch file, forces ``HFO2_FERROKG_USE_LLM=false``,
blanks ``DASHSCOPE_API_KEY`` / ``OPENAI_API_KEY``, asserts all three hold, and
fingerprints both the database and ``data/`` around the run.

Known writers (measured by running each test file alone against a fingerprint)
-----------------------------------------------------------------------------
    tests/test_llm_extractor.py          data/extraction_candidates/, data/ontology/
    tests/test_multi_model_validator.py  data/exports/model_validation_*
    tests/test_computation_validation.py data/computation/validation_jobs/
    tests/test_computation_workflow.py   data/computation/simulation_runtime/

Fixing them is not a one-line change per test:

  * ``simulation_runtime.check_simulation_runtime`` binds
    ``status_path=DEFAULT_STATUS_PATH`` as a default argument, so rebasing the
    module constant after import has no effect -- the default was captured at
    def time. The function object's ``__defaults__`` would have to be patched,
    or the call site in ``computation_workflow`` changed to pass it through.
  * ``PROJECT_ROOT`` cannot simply be patched to a temp dir. 48 of its uses
    point outside ``data/``, and some are call-time reads of tracked source
    files -- ``llm_extractor`` reads ``prompts/hfo2_extraction_prompt.md`` at
    call time and would break. Module-level constants such as
    ``ontology_builder.ONTOLOGY_SOURCE`` are bound at import and therefore
    survive a later ``PROJECT_ROOT`` patch, but that must be checked per module
    rather than assumed.

This file is kept byte-identical between the main repository and the
``hfo2-phase-competition-worktree`` worktree. Edit both, or edit one and copy.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROTECTED_RUNTIME_DIRS = [
    PROJECT_ROOT / "data" / "exports",
    PROJECT_ROOT / "data" / "extraction_candidates",
    PROJECT_ROOT / "data" / "ontology",
    PROJECT_ROOT / "data" / "computation",
]

# Deliberate blind spot, not an oversight.
#
# data/computation/tools/ holds the vendored FerroX/AMReX toolchain source
# (simulation_runtime.DEFAULT_FERROX_SOURCE). It is 2,921 of the 3,447 files
# under data/computation -- 85% -- and stat-walking it over the /mnt/d 9p mount
# cost ~60 s per session. It is third-party source, not a research artefact this
# project generates, it is no service's output target, and no test has ever
# written there. Excluding it brings the main repository's suite from ~94 s back
# to ~30 s.
#
# If a service ever starts writing under tools/, add a narrower watch entry for
# that specific subtree rather than removing this exclusion.
PROTECTED_EXCLUDES = [
    PROJECT_ROOT / "data" / "computation" / "tools",
]


def _is_excluded(path: Path) -> bool:
    return any(path == ex or ex in path.parents for ex in PROTECTED_EXCLUDES)


def _fingerprint(roots):
    """Map relpath -> (size, mtime_ns) for every watched file.

    Uses os.walk rather than Path.rglob so that excluded directories are pruned
    from the traversal instead of enumerated and filtered afterwards. Filtering
    after rglob still pays the full directory walk, which on the /mnt/d 9p mount
    is the dominant cost: pruning data/computation/tools (2,921 of 3,447 files)
    only actually helped once the walk stopped descending into it.
    """
    entries = {}
    for root in roots:
        if not root.exists():
            entries[root.relative_to(PROJECT_ROOT).as_posix()] = ("MISSING", -1)
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            here = Path(dirpath)
            # prune in place so os.walk does not descend
            dirnames[:] = sorted(
                d for d in dirnames if not _is_excluded(here / d)
            )
            for name in sorted(filenames):
                path = here / name
                if _is_excluded(path):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                entries[path.relative_to(PROJECT_ROOT).as_posix()] = (
                    stat.st_size,
                    stat.st_mtime_ns,
                )
    return entries


@pytest.fixture(scope="session", autouse=True)
def prevent_production_output_pollution():
    before = _fingerprint(PROTECTED_RUNTIME_DIRS)
    yield
    after = _fingerprint(PROTECTED_RUNTIME_DIRS)

    changed = []
    for rel in sorted(set(before) | set(after)):
        b, a = before.get(rel), after.get(rel)
        if b == a:
            continue
        if b is None:
            changed.append((rel, "created"))
        elif a is None:
            changed.append((rel, "deleted"))
        elif b[0] != a[0]:
            changed.append((rel, "size %d -> %d" % (b[0], a[0])))
        else:
            changed.append((rel, "rewritten (same size, mtime moved)"))

    if not changed:
        return

    listing = "\n".join("  - %s  (%s)" % (rel, why) for rel, why in changed[:40])
    more = "\n  ... and %d more" % (len(changed) - 40) if len(changed) > 40 else ""
    raise AssertionError(
        "Tests modified protected runtime output files:\n"
        "%s%s\n\n"
        "Tests must use tmp_path, an explicit output_dir, or monkeypatched "
        "paths instead of writing into the real data tree. See this module's "
        "docstring for the measured list of offending test files and for why "
        "the obvious fixes do not work." % (listing, more)
    )
