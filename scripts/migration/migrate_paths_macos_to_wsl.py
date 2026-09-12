"""macOS -> WSL path migration for the HfO2-FerroKG production database.

Rewrites the live path-pointer columns so that stored paths resolve from the
WSL side of the migrated project tree.

    python scripts/migration/migrate_paths_macos_to_wsl.py --dry-run
    python scripts/migration/migrate_paths_macos_to_wsl.py --apply

--apply refuses to run unless it can first create a SHA-256-verified backup of
the database, and commits every rewrite in a single transaction.

Deliberately NOT rewritten:

  pipeline_runs.stats_json
      Append-only historical run log (run_id, step_name, status, stats_json,
      message, created_at). The paths inside record where a past run wrote its
      output at the time it ran. Only backend/services/pipeline_log.py touches
      the column - record_pipeline_run() writes it, recent_pipeline_runs()
      reads it back for display. Nothing resolves a path from it. Rewriting it
      would falsify the record of where a run actually happened, which
      conflicts with the evidence-traceability rules in AGENTS.md.

  reports/**
      Hash-bound audit artefacts. Edit-by-regeneration only; see
      docs/windows_migration_20260912.md section 5.2.

This script was used for the 2026-09-12 migration (52,310 rows). See
docs/windows_migration_20260912.md for the full record and the rollback point.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WORKTREE_ROOT = SCRIPT_DIR.parents[1]
CODEX_ROOT = SCRIPT_DIR.parents[2]

# The macOS source prefix is redacted from this repository, so the default is a
# placeholder and the rewrite matches nothing. That is the correct behaviour for
# an already-migrated database (--dry-run reports 0 rows either way). To run this
# against another machine, supply the real prefix explicitly:
#
#   HFO2_MAC_SOURCE_PREFIX="/Users/<real-user>/Codex/" \
#       python scripts/migration/migrate_paths_macos_to_wsl.py --dry-run
MAC_PREFIX = os.environ.get("HFO2_MAC_SOURCE_PREFIX", "/Users/<mac-user>/Codex/")

# Detection pattern for "a macOS home path is still stored here". Deliberately
# username-independent: it matches any /Users/<name>/... value, so it keeps
# working after the real prefix is redacted, and it is strictly more general than
# matching a single account name.
MAC_PATH_LIKE = "%/Users/%"

# Historical pre-migration Windows location of the project. Not derivable from
# the current tree, so it stays an explicit constant (override with
# --old-win-prefix if another stale root turns up).
OLD_WIN_PREFIX = "D:\\KG agent\\hfo2-ferro-kg"


def windows_to_wsl(path: Path) -> str:
    """D:\\Code-X\\foo -> /mnt/d/Code-X/foo. Non-Windows paths pass through."""
    s = str(path)
    if len(s) >= 2 and s[1] == ":" and s[0].isalpha():
        return "/mnt/%s%s" % (s[0].lower(), s[2:].replace("\\", "/"))
    return s.replace("\\", "/")


def resolve_main_repo(explicit: str | None) -> Path:
    """Locate the repo that owns the production database.

    Prefers the worktree's own .git pointer over a guessed relative layout, so
    the script keeps working if the tree is moved or re-linked.
    """
    if explicit:
        return Path(explicit).resolve()
    gitfile = WORKTREE_ROOT / ".git"
    if gitfile.is_file():
        for line in gitfile.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("gitdir:"):
                gd = Path(line.split(":", 1)[1].strip())
                # .../<main repo>/.git/worktrees/<name>  ->  <main repo>
                if gd.parts[-3:-2] == (".git",):
                    return Path(*gd.parts[:-3])
                if gd.name == ".git":
                    return gd.parent
    if (WORKTREE_ROOT / ".git").is_dir():
        return WORKTREE_ROOT
    return (CODEX_ROOT / "Ferroelectric knowledgegraph" / "KG agent" / "hfo2-ferro-kg").resolve()


# Live pointer columns whose whole value is a single path.
PLAIN = [
    ("multimodal_asset_queue", "file_path"),
    ("pdf_visual_assets", "file_path"),
    ("pdf_equations", "image_path"),
    ("pdf_files", "file_path"),
    ("literature_candidates", "downloaded_pdf_path"),
    ("computation_jobs", "work_dir"),
    ("ontology_versions", "bundle_path"),
    ("ontology_versions", "report_path"),
]
# Columns holding a JSON document with paths embedded inside it.
JSONCOLS = [
    ("computation_jobs", "input_manifest_json"),
    ("computation_jobs", "cloud_payload_json"),
]


class Mapper:
    def __init__(self, wsl_prefix: str, new_wsl_root: str, old_win_prefix: str):
        self.wsl_prefix = wsl_prefix
        self.new_wsl_root = new_wsl_root
        self.old_win_prefix = old_win_prefix

    def plain(self, value):
        if value is None:
            return None, False
        out = value
        if MAC_PREFIX in out:
            out = out.replace(MAC_PREFIX, self.wsl_prefix)
        if out.startswith(self.old_win_prefix):
            tail = out[len(self.old_win_prefix):]
            out = self.new_wsl_root + tail.replace("\\", "/")
        return out, out != value

    def json_doc(self, value):
        if value is None or value == "":
            return None, False
        try:
            obj = json.loads(value)
        except Exception:
            return value, False

        def walk(node):
            if isinstance(node, str):
                new = node
                if MAC_PREFIX in new:
                    new = new.replace(MAC_PREFIX, self.wsl_prefix)
                if new.startswith(self.old_win_prefix):
                    new = self.new_wsl_root + new[len(self.old_win_prefix):].replace("\\", "/")
                return new
            if isinstance(node, dict):
                return {k: walk(v) for k, v in node.items()}
            if isinstance(node, list):
                return [walk(v) for v in node]
            return node

        new_text = json.dumps(walk(obj), ensure_ascii=False, sort_keys=True)
        return new_text, new_text != value


def sha256_of(path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def check_no_lock(db: str) -> None:
    for ext in ("-wal", "-shm", "-journal"):
        if os.path.exists(db + ext):
            sys.exit("ABORT: sidecar %s exists - close anything using the DB first" % (db + ext))
    con = sqlite3.connect(db, timeout=3)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.rollback()
    except sqlite3.OperationalError as e:
        sys.exit("ABORT: database is locked (%s)" % e)
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    g.add_argument("--apply", action="store_true", help="back up, then rewrite in one transaction")
    ap.add_argument("--main-repo", help="repo owning the production DB (default: from the worktree .git pointer)")
    ap.add_argument("--db", help="database path (default: <main-repo>/data/hfo2_ferrokg.sqlite3)")
    ap.add_argument("--backup-dir", help="backup destination (default: <main-repo>/data/backups)")
    ap.add_argument("--wsl-prefix", help="replacement for %s (default: derived from the tree location)" % MAC_PREFIX)
    ap.add_argument("--old-win-prefix", default=OLD_WIN_PREFIX,
                    help="stale Windows root to remap (default: %(default)s)")
    args = ap.parse_args()

    main_repo = resolve_main_repo(args.main_repo)
    db = args.db or str(main_repo / "data" / "hfo2_ferrokg.sqlite3")
    backup_dir = args.backup_dir or str(main_repo / "data" / "backups")
    wsl_prefix = args.wsl_prefix or windows_to_wsl(CODEX_ROOT) + "/"
    new_wsl_root = windows_to_wsl(main_repo)

    if not wsl_prefix.endswith("/"):
        wsl_prefix += "/"

    if not os.path.exists(db):
        sys.exit("ABORT: database not found: %s" % db)

    mapper = Mapper(wsl_prefix, new_wsl_root, args.old_win_prefix)

    print("main repo  :", main_repo)
    print("database   :", db)
    print("size       : {:,} bytes".format(os.path.getsize(db)))
    print("mapping    :")
    print("   %s-> %s" % (MAC_PREFIX.ljust(34), wsl_prefix))
    print("   %s-> %s" % ((args.old_win_prefix + "\\").ljust(34), new_wsl_root + "/"))
    print("mode       :", "DRY RUN (no writes)" if args.dry_run else "APPLY")
    print()

    if args.apply:
        check_no_lock(db)
        print("no lock/sidecar: OK")
        os.makedirs(backup_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup = os.path.join(backup_dir, "hfo2_ferrokg_before_windows_migration_%s.sqlite3" % stamp)
        print("backing up -> %s" % backup)
        t0 = time.time()
        shutil.copy2(db, backup)
        print("  copied in {:.1f}s, {:,} bytes".format(time.time() - t0, os.path.getsize(backup)))
        src_hash, bak_hash = sha256_of(db), sha256_of(backup)
        print("  sha256 source :", src_hash)
        print("  sha256 backup :", bak_hash)
        if src_hash != bak_hash:
            os.remove(backup)
            sys.exit("ABORT: backup hash mismatch, removed backup")
        print("  backup verified identical")
        print()

    con = sqlite3.connect(db, timeout=30)
    con.row_factory = sqlite3.Row
    if args.dry_run:
        con.execute("PRAGMA query_only=ON")

    grand = 0
    per_col = []
    samples = []
    updates = []

    for t, c in PLAIN:
        rows = con.execute('SELECT rowid AS rid, "%s" AS v FROM "%s"' % (c, t)).fetchall()
        n = 0
        for r in rows:
            new, changed = mapper.plain(r["v"])
            if changed:
                n += 1
                if len(samples) < 6:
                    samples.append((t, c, r["v"], new))
                if args.apply:
                    updates.append(('UPDATE "%s" SET "%s"=? WHERE rowid=?' % (t, c), new, r["rid"]))
        per_col.append((t + "." + c, n, len(rows)))
        grand += n

    for t, c in JSONCOLS:
        rows = con.execute('SELECT rowid AS rid, "%s" AS v FROM "%s"' % (c, t)).fetchall()
        n = 0
        for r in rows:
            new, changed = mapper.json_doc(r["v"])
            if changed:
                n += 1
                if args.apply:
                    updates.append(('UPDATE "%s" SET "%s"=? WHERE rowid=?' % (t, c), new, r["rid"]))
        per_col.append((t + "." + c, n, len(rows)))
        grand += n

    print("%-46s %9s %9s" % ("column", "to_change", "total"))
    print("-" * 66)
    for name, n, tot in per_col:
        print("%-46s %9d %9d" % (name, n, tot))
    print("-" * 66)
    print("%-46s %9d" % ("GRAND TOTAL rows to rewrite", grand))

    if samples:
        print("\nsample transformations:")
        for t, c, old, new in samples:
            print("  %s.%s\n    - %s\n    + %s" % (t, c, old[:130], new[:130]))

    if args.dry_run:
        print("\nDRY RUN complete. Nothing written.")
        con.close()
        return 0

    print("\napplying %d updates in a single transaction..." % len(updates))
    t0 = time.time()
    cur = con.cursor()
    try:
        cur.execute("BEGIN")
        for sql, val, rid in updates:
            cur.execute(sql, (val, rid))
        con.commit()
    except Exception:
        con.rollback()
        con.close()
        raise
    print("  committed in {:.1f}s".format(time.time() - t0))
    con.close()

    print("\nPOST-VERIFICATION")
    con = sqlite3.connect("file:" + str(Path(db).as_posix()) + "?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    leftover = 0
    for t, c in PLAIN + JSONCOLS:
        n = con.execute('SELECT COUNT(*) FROM "%s" WHERE CAST("%s" AS TEXT) LIKE ?' % (t, c),
                        (MAC_PATH_LIKE,)).fetchone()[0]
        n2 = con.execute('SELECT COUNT(*) FROM "%s" WHERE CAST("%s" AS TEXT) LIKE ?' % (t, c),
                         (args.old_win_prefix + "%",)).fetchone()[0]
        if n or n2:
            print("  !! %s.%s still has mac=%d oldwin=%d" % (t, c, n, n2))
        leftover += n + n2
    print("  leftover stale paths in rewritten columns:", leftover)

    print("  JSON validity:")
    for t, c in JSONCOLS + [("pipeline_runs", "stats_json")]:
        ok = bad = 0
        for (v,) in con.execute('SELECT CAST("%s" AS TEXT) FROM "%s"' % (c, t)):
            if not v:
                continue
            try:
                json.loads(v)
                ok += 1
            except Exception:
                bad += 1
        print("    %-40s valid=%-5d invalid=%d" % (t + "." + c, ok, bad))

    print("  stats_json left untouched (historical log): mac rows =",
          con.execute("SELECT COUNT(*) FROM pipeline_runs WHERE stats_json LIKE ?",
                      (MAC_PATH_LIKE,)).fetchone()[0])
    con.close()
    return 0 if leftover == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
