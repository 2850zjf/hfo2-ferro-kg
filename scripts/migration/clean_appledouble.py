"""Remove macOS AppleDouble / Finder residue left by a mac->Windows copy.

    python scripts/migration/clean_appledouble.py --dry-run
    python scripts/migration/clean_appledouble.py --apply

Deletion policy - all four must hold:

  1. basename starts with "._", or basename == ".DS_Store"
  2. for "._" files, the first 4 bytes are the AppleDouble magic 00 05 16 07
     (.DS_Store is matched on name alone - it is a Finder artefact)
  3. the path is NOT tracked by git in its owning repository
  4. the file is readable enough to test (2)

A file whose name matches but whose magic bytes do not is reported and left
alone, so a real file that happens to start with "._" is never removed.
If git cannot enumerate tracked files for a repository, that repository is
skipped entirely rather than cleaned unsafely.

2026-09-12 run: 58,587 files / 240.0 MB removed from the main repo
(173 of them inside .git/), 0 from the worktree, 0 blocked by (2) or (3).
See docs/windows_migration_20260912.md section 6.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WORKTREE_ROOT = SCRIPT_DIR.parents[1]
CODEX_ROOT = SCRIPT_DIR.parents[2]

APPLEDOUBLE_MAGIC = b"\x00\x05\x16\x07"


def default_roots() -> list[Path]:
    """Worktree plus the repo that owns it, resolved from the .git pointer."""
    roots = [WORKTREE_ROOT]
    gitfile = WORKTREE_ROOT / ".git"
    if gitfile.is_file():
        for line in gitfile.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("gitdir:"):
                gd = Path(line.split(":", 1)[1].strip())
                if gd.parts[-3:-2] == (".git",):
                    main = Path(*gd.parts[:-3])
                    if main not in roots:
                        roots.append(main)
    return roots


def git_tracked(repo: Path):
    try:
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"],
                             capture_output=True, timeout=900)
    except Exception as e:
        print("  git ls-files failed for %s: %s" % (repo, e))
        return None
    if out.returncode != 0:
        print("  git ls-files rc=%d for %s: %s"
              % (out.returncode, repo, out.stderr.decode("utf-8", "replace")[:200]))
        return None
    return {p for p in out.stdout.decode("utf-8", "replace").split("\0") if p}


def classify(path: str):
    base = os.path.basename(path)
    if base == ".DS_Store":
        return "delete", "ds_store"
    if base.startswith("._"):
        try:
            with open(path, "rb") as f:
                head = f.read(4)
        except OSError as e:
            return "keep", "unreadable:%s" % e
        if head == APPLEDOUBLE_MAGIC:
            return "delete", "appledouble"
        return "keep", "name_matches_but_magic_is_%s" % (head.hex() or "empty")
    return "keep", "not_a_candidate"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--root", action="append",
                    help="repository to clean (repeatable; default: worktree + owning repo)")
    args = ap.parse_args()

    roots = [Path(r).resolve() for r in args.root] if args.root else default_roots()

    print("mode :", "DRY RUN" if args.dry_run else "APPLY")
    print("roots:", ", ".join(str(r) for r in roots))

    grand_files = grand_bytes = 0
    kept_report = []

    for repo in roots:
        if not repo.is_dir():
            print("\nSKIP (missing): %s" % repo)
            continue
        print("\n" + "=" * 74)
        print("repo:", repo)
        print("=" * 74)

        tracked = git_tracked(repo)
        if tracked is None:
            print("  ABORT this repo: cannot enumerate git-tracked files")
            continue
        print("  git-tracked files:", len(tracked))

        n_del = n_keep = n_blocked = n_in_git = b_del = 0
        for dirpath, _dirnames, filenames in os.walk(repo):
            for fn in filenames:
                if not (fn.startswith("._") or fn == ".DS_Store"):
                    continue
                full = os.path.join(dirpath, fn)
                r = os.path.relpath(full, repo).replace(os.sep, "/")
                if r in tracked:
                    n_blocked += 1
                    kept_report.append((repo.name, r, "TRACKED BY GIT - not deleted"))
                    continue
                verdict, reason = classify(full)
                if verdict != "delete":
                    n_keep += 1
                    kept_report.append((repo.name, r, reason))
                    continue
                if r.startswith(".git/"):
                    n_in_git += 1
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                if args.apply:
                    try:
                        os.remove(full)
                    except OSError as e:
                        kept_report.append((repo.name, r, "delete failed: %s" % e))
                        continue
                n_del += 1
                b_del += size

        print("  to delete : %d files, %.1f MB" % (n_del, b_del / 1e6))
        print("     of which inside .git/ :", n_in_git)
        print("  kept      : %d (name matched but failed a safety test)" % n_keep)
        print("  blocked   : %d (tracked by git)" % n_blocked)
        grand_files += n_del
        grand_bytes += b_del

    print("\n" + "=" * 74)
    print("TOTAL %s: %d files, %.1f MB"
          % ("deleted" if args.apply else "would delete", grand_files, grand_bytes / 1e6))
    if kept_report:
        print("\nNOT deleted (%d):" % len(kept_report))
        for name, r, why in kept_report[:40]:
            print("  [%s] %s  ->  %s" % (name, r[:90], why))
        if len(kept_report) > 40:
            print("  ... and %d more" % (len(kept_report) - 40))
    return 0


if __name__ == "__main__":
    sys.exit(main())
