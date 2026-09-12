#!/usr/bin/env bash
# Run the MAIN repository's pytest under isolation.
#
# WHY THIS EXISTS
# ---------------
# The main repository is not safe to `pytest` directly. Three hazards, all
# verified rather than assumed:
#
#   1. Its backend/core/config.py derives
#          db_path = PROJECT_ROOT / "data/hfo2_ferrokg.sqlite3"
#      and in the main repository that IS the ~1 GB production database.
#      backend/db/session.py:connect() opens it read-write and calls
#      init_database(), which can create or alter tables.
#
#   2. It has no tests/conftest.py pollution guard, and four of its test files
#      write straight into the production data tree:
#          test_llm_extractor.py        -> data/extraction_candidates/, data/ontology/
#          test_multi_model_validator.py -> data/exports/model_validation_*
#          test_computation_validation.py-> data/computation/validation_jobs/
#          test_computation_workflow.py  -> data/computation/simulation_runtime/
#
#   3. Its .env sets HFO2_FERROKG_USE_LLM=true and carries a real
#      DASHSCOPE_API_KEY, and config.py calls load_dotenv(PROJECT_ROOT/".env"),
#      so a test that reaches the LLM client would spend real money.
#
# python-dotenv's load_dotenv defaults to override=False, so exporting these
# variables before the run wins over .env. Step 1 asserts that rather than
# trusting it.
#
# The production database and every watched data/ subtree are fingerprinted
# before and after, so "nothing was touched" is evidence and not intention.
#
# Usage:
#   scripts/migration/run_main_repo_pytest.sh              # run the suite
#   scripts/migration/run_main_repo_pytest.sh tests/test_x.py -k foo
#
# Exit code is pytest's.

set -uo pipefail

# Resolve the main repository from this worktree's .git pointer, so the script
# keeps working if the tree is moved. Falls back to the sibling layout.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKTREE="$(cd "$HERE/../.." && pwd)"
GITFILE="$WORKTREE/.git"

MAIN=""
if [ -f "$GITFILE" ]; then
  ptr="$(sed -n 's/^gitdir:[[:space:]]*//p' "$GITFILE" | head -1)"
  case "$ptr" in
    /*) gd="$ptr" ;;
    *)  gd="$WORKTREE/$ptr" ;;
  esac
  # $gd is <main repo>/.git/worktrees/<name>; dirname drops <name>, so two
  # more levels up (<- worktrees, <- .git) reaches the main repository root.
  cand="$(cd "$(dirname "$gd")/../.." 2>/dev/null && pwd || true)"
  [ -n "$cand" ] && MAIN="$cand"
fi
[ -z "$MAIN" ] && MAIN="$(cd "$WORKTREE/.." && pwd)/Ferroelectric knowledgegraph/KG agent/hfo2-ferro-kg"

if [ ! -d "$MAIN" ]; then
  echo "ABORT: could not resolve the main repository (got: $MAIN)" >&2
  exit 2
fi

VENV_PY="${HFO2_VENV_PY:-$HOME/.venvs/hfo2-ferrokg/bin/python}"
if [ ! -x "$VENV_PY" ]; then
  echo "ABORT: no python at $VENV_PY (set HFO2_VENV_PY to override)" >&2
  exit 2
fi

SCRATCH_DB="${HFO2_SCRATCH_DB:-/tmp/hfo2_mainrepo_pytest.sqlite3}"
LOG="${HFO2_PYTEST_LOG:-/tmp/pytest_main.log}"
WATCH="data/exports data/extraction_candidates data/ontology data/computation"

# ---- isolation: these must beat .env ---------------------------------------
export HFO2_FERROKG_DB_PATH="$SCRATCH_DB"
export HFO2_FERROKG_USE_LLM=false
export DASHSCOPE_API_KEY=''
export OPENAI_API_KEY=''

cd "$MAIN" || exit 2
rm -f "$SCRATCH_DB" "$SCRATCH_DB-wal" "$SCRATCH_DB-shm"

fingerprint() {
  find $WATCH -type f -printf '%p %s %T@\n' 2>/dev/null | LC_ALL=C sort
}

echo "main repo : $MAIN"
echo "python    : $VENV_PY"
echo "scratch db: $SCRATCH_DB"
echo

echo "=============================================================="
echo "1. ASSERT THE ISOLATION ACTUALLY HOLDS"
echo "=============================================================="
PYTHONPATH=. "$VENV_PY" - <<'PY'
import os, sys
from backend.core.config import get_settings, get_llm_api_key
s = get_settings()
prod = os.path.join(os.getcwd(), "data", "hfo2_ferrokg.sqlite3")
print("  db_path   :", s.db_path)
print("  use_llm   :", s.use_llm)
print("  api_key   :", get_llm_api_key())
print("  pdf_root  :", s.pdf_root)
bad = []
if os.path.abspath(str(s.db_path)) == os.path.abspath(prod):
    bad.append("db_path is the PRODUCTION database")
if s.use_llm is not False:
    bad.append("use_llm is not False")
if get_llm_api_key() is not None:
    bad.append("an API key is reachable")
if bad:
    print("  ABORT:", "; ".join(bad))
    sys.exit(1)
print("  -> isolation OK: scratch db, llm off, no key")
PY
[ $? -ne 0 ] && { echo "ABORT: isolation check failed; not running pytest." >&2; exit 2; }

echo
echo "=============================================================="
echo "2. PRODUCTION DB HASH - BEFORE"
echo "=============================================================="
sha256sum "$MAIN/data/hfo2_ferrokg.sqlite3" | tee /tmp/_db_before.txt

echo
echo "=============================================================="
echo "3. data/ FINGERPRINT - BEFORE"
echo "=============================================================="
fingerprint > /tmp/_fp_before.txt
echo "  $(wc -l < /tmp/_fp_before.txt) files watched under: $WATCH"

echo
echo "=============================================================="
echo "4. PYTEST"
echo "=============================================================="
"$VENV_PY" -m pytest -v --tb=short -rf "$@" > "$LOG" 2>&1
rc=$?
echo "PYTEST EXIT=$rc   (full log: $LOG)"
grep -E '^(FAILED|ERROR)' "$LOG" | head -40
echo "--- summary ---"
grep -E '[0-9]+ (passed|failed|error)' "$LOG" | tail -3

echo
echo "=============================================================="
echo "5. PRODUCTION DB HASH - AFTER"
echo "=============================================================="
sha256sum "$MAIN/data/hfo2_ferrokg.sqlite3" | tee /tmp/_db_after.txt
if diff -q /tmp/_db_before.txt /tmp/_db_after.txt >/dev/null; then
  echo "  -> UNCHANGED"
else
  echo "  -> !!! PRODUCTION DB CHANGED !!!"
  rc=3
fi

echo
echo "=============================================================="
echo "6. data/ FINGERPRINT - AFTER"
echo "=============================================================="
fingerprint > /tmp/_fp_after.txt
if diff -u /tmp/_fp_before.txt /tmp/_fp_after.txt > /tmp/_fp_diff.txt; then
  echo "  -> no watched data/ file changed"
else
  echo "  -> !!! data/ WAS WRITTEN (test isolation defect) !!!"
  head -40 /tmp/_fp_diff.txt
  rc=3
fi

echo
echo "=============================================================="
echo "7. SCRATCH DB PRODUCED BY THE RUN"
echo "=============================================================="
ls -la "$SCRATCH_DB" 2>/dev/null || echo "  (none)"

exit $rc
