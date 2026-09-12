"""Post-migration verification for the macOS -> WSL path rewrite. Read-only.

    python scripts/migration/verify_migration.py
    python scripts/migration/verify_migration.py --sample 0     # 0 = check every row

Reports, in order:
  1. residual stale paths in every rewritten column   (expect 0)
  2. that the deliberately preserved historical log is still intact
  3. PRAGMA integrity_check / quick_check and JSON validity
  4. on-disk existence of the rewritten pointers
  5. AppleDouble residue
  6. that the pre-migration backup is present
  7. the .gitignore guard rule

Exit code is non-zero if any check fails.

Step 4 resolves a stored /mnt/<drive>/... value against the local filesystem.
Run from WSL it checks the path directly; run from Windows it remaps
/mnt/d/ -> D:/ first. Both are supported because the project is used from
either side.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WORKTREE_ROOT = SCRIPT_DIR.parents[1]
CODEX_ROOT = SCRIPT_DIR.parents[2]

# Detection pattern for "a macOS home path is still stored here". Deliberately
# username-independent so it keeps working after the real source prefix was
# redacted from this repository, and stricter than matching one account name:
# it flags any /Users/<name>/... value.
MAC_PATH_LIKE = "%/Users/%"

REWRITTEN = [
    ("multimodal_asset_queue", "file_path"),
    ("pdf_visual_assets", "file_path"),
    ("pdf_equations", "image_path"),
    ("pdf_files", "file_path"),
    ("literature_candidates", "downloaded_pdf_path"),
    ("computation_jobs", "work_dir"),
    ("ontology_versions", "bundle_path"),
    ("ontology_versions", "report_path"),
    ("computation_jobs", "input_manifest_json"),
    ("computation_jobs", "cloud_payload_json"),
]
PRESERVED = [("pipeline_runs", "stats_json")]
EXISTENCE_CHECK = [
    ("pdf_files", "file_path"),
    ("pdf_equations", "image_path"),
    ("pdf_visual_assets", "file_path"),
    ("multimodal_asset_queue", "file_path"),
    ("ontology_versions", "bundle_path"),
    ("ontology_versions", "report_path"),
    ("literature_candidates", "downloaded_pdf_path"),
    ("computation_jobs", "work_dir"),
]

_MNT = re.compile(r"^/mnt/([a-z])(/.*)$")


def resolve_local(value: str) -> str:
    """Map a stored WSL path onto something os.path.exists can test here.

    Try the value as-is first. Under WSL a stored /mnt/d/... path is already
    correct, and rewriting it to D:\\... would make every check fail. Only when
    the value does not resolve do we attempt the Windows drive-letter form, which
    is what makes this script usable from the Windows side too.
    """
    if os.path.exists(value):
        return value
    m = _MNT.match(value)
    if not m:
        return value
    drive, rest = m.group(1).upper(), m.group(2)
    return ("%s:%s" % (drive, rest)).replace("/", os.sep)


def resolve_main_repo(explicit):
    if explicit:
        return Path(explicit).resolve()
    gitfile = WORKTREE_ROOT / ".git"
    if gitfile.is_file():
        for line in gitfile.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("gitdir:"):
                gd = Path(line.split(":", 1)[1].strip())
                if gd.parts[-3:-2] == (".git",):
                    return Path(*gd.parts[:-3])
    return WORKTREE_ROOT


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--main-repo", help="repo owning the production DB")
    ap.add_argument("--db", help="database path (default: <main-repo>/data/hfo2_ferrokg.sqlite3)")
    ap.add_argument("--sample", type=int, default=0,
                    help="max rows to existence-check per column; 0 = all (default)")
    args = ap.parse_args()

    main_repo = resolve_main_repo(args.main_repo)
    db = args.db or str(main_repo / "data" / "hfo2_ferrokg.sqlite3")
    if not os.path.exists(db):
        sys.exit("ABORT: database not found: %s" % db)

    failures = []
    con = sqlite3.connect("file:" + Path(db).as_posix() + "?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")

    print("=" * 74)
    print("database:", db)
    print("=" * 74)

    print("\n1. REWRITTEN COLUMNS - residual stale paths (expect 0)")
    residual = 0
    for t, c in REWRITTEN:
        mac = con.execute('SELECT COUNT(*) FROM "%s" WHERE CAST("%s" AS TEXT) LIKE ?' % (t, c),
                          (MAC_PATH_LIKE,)).fetchone()[0]
        old = con.execute('SELECT COUNT(*) FROM "%s" WHERE CAST("%s" AS TEXT) LIKE ?' % (t, c),
                          ("D:\\KG agent%",)).fetchone()[0]
        wsl = con.execute('SELECT COUNT(*) FROM "%s" WHERE CAST("%s" AS TEXT) LIKE ?' % (t, c),
                          ("%mnt/%",)).fetchone()[0]
        residual += mac + old
        print("  %-44s mac=%-5d oldwin=%-4d wsl=%d" % (t + "." + c, mac, old, wsl))
    print("  -> residual stale: %d" % residual)
    if residual:
        failures.append("residual stale paths: %d" % residual)

    print("\n2. PRESERVED COLUMNS - historical log intact (expect non-zero)")
    for t, c in PRESERVED:
        mac = con.execute('SELECT COUNT(*) FROM "%s" WHERE CAST("%s" AS TEXT) LIKE ?' % (t, c),
                          (MAC_PATH_LIKE,)).fetchone()[0]
        rows = con.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
        print("  %-44s mac rows=%d of %d total (preserved)" % (t + "." + c, mac, rows))
        if mac == 0:
            failures.append("preserved log appears to have been rewritten")

    print("\n3. INTEGRITY + JSON VALIDITY")
    for pragma in ("integrity_check", "quick_check"):
        val = con.execute("PRAGMA %s" % pragma).fetchone()[0]
        print("  PRAGMA %-16s: %s" % (pragma, val))
        if val != "ok":
            failures.append("%s returned %s" % (pragma, val))
    for t, c in [("computation_jobs", "input_manifest_json"),
                 ("computation_jobs", "cloud_payload_json"),
                 ("pipeline_runs", "stats_json")]:
        ok = bad = 0
        for (v,) in con.execute('SELECT CAST("%s" AS TEXT) FROM "%s"' % (c, t)):
            if not v:
                continue
            try:
                json.loads(v)
                ok += 1
            except Exception:
                bad += 1
        print("  %-44s valid=%-5d invalid=%d" % (t + "." + c, ok, bad))
        if bad:
            failures.append("%s.%s has %d invalid JSON rows" % (t, c, bad))

    print("\n4. REWRITTEN POINTER EXISTENCE (%s)"
          % ("full population" if args.sample == 0 else "sample=%d/col" % args.sample))
    for t, c in EXISTENCE_CHECK:
        limit = "" if args.sample == 0 else " LIMIT %d" % args.sample
        tot = miss = 0
        shown = 0
        for (v,) in con.execute('SELECT "%s" FROM "%s" WHERE "%s" LIKE ?%s' % (c, t, c, limit),
                                ("/mnt/%",)):
            tot += 1
            if not os.path.exists(resolve_local(v)):
                miss += 1
                if shown < 3:
                    print("      MISSING: %s" % v[:130])
                    shown += 1
        print("  %-44s rows=%-6d missing=%d" % (t + "." + c, tot, miss))
        if miss:
            failures.append("%s.%s: %d rewritten paths do not exist" % (t, c, miss))

    print("\n5. APPLEDOUBLE RESIDUE")
    for label, root in (("main", main_repo), ("worktree", WORKTREE_ROOT)):
        n = sum(1 for _dp, _dn, fns in os.walk(root) for fn in fns
                if fn.startswith("._") or fn == ".DS_Store")
        print("  %-9s remaining ._* / .DS_Store files: %d" % (label, n))
        if n:
            failures.append("%s still has %d AppleDouble files" % (label, n))

    print("\n6. BACKUP PRESENT")
    bdir = main_repo / "data" / "backups"
    found = 0
    if bdir.is_dir():
        for f in sorted(os.listdir(bdir)):
            if "windows_migration" in f:
                found += 1
                print("  {:,} bytes  {}".format(os.path.getsize(bdir / f), f))
    if not found:
        print("  none found")
        failures.append("no pre-migration backup found in %s" % bdir)

    print("\n7. .gitignore GUARD")
    for label, root in (("main", main_repo), ("worktree", WORKTREE_ROOT)):
        gi = root / ".gitignore"
        has = gi.is_file() and any(
            l.strip() == "._*" for l in gi.read_text(encoding="utf-8", errors="replace").splitlines())
        print("  %-9s has '._*': %s" % (label, has))
        if not has:
            failures.append("%s .gitignore lacks the '._*' guard" % label)

    con.close()
    print("\n" + "=" * 74)
    if failures:
        print("FAILED (%d):" % len(failures))
        for f in failures:
            print("  -", f)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
