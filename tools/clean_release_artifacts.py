"""Remove local artifacts before a bridge-only release."""

from __future__ import annotations

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR_NAMES = {"__pycache__", ".pytest_cache"}
ARTIFACT_SUFFIXES = {".pyc", ".pyo"}
LOCAL_STATE_DIR_NAMES = {".blib_hou_workflows", ".test_home"}


def collect_artifacts(root: Path | str = ROOT) -> list[Path]:
    base = Path(root)
    artifacts: list[Path] = []
    for name in LOCAL_STATE_DIR_NAMES:
        path = base / name
        if path.exists():
            artifacts.append(path)
    for path in base.rglob("*"):
        if path.is_dir() and path.name in ARTIFACT_DIR_NAMES:
            artifacts.append(path)
        elif path.is_file() and path.suffix in ARTIFACT_SUFFIXES:
            artifacts.append(path)
    return sorted(artifacts, key=lambda item: (len(item.parts), item.as_posix()), reverse=True)


def clean_artifacts(root: Path | str = ROOT, dry_run: bool = False) -> list[Path]:
    removed: list[Path] = []
    for path in collect_artifacts(root):
        if not path.exists():
            continue
        if not dry_run:
            if path.is_dir():
                for child in sorted(path.rglob("*"), key=lambda item: len(item.parts), reverse=True):
                    if child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        child.rmdir()
                path.rmdir()
            else:
                path.unlink()
        removed.append(path)
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clean_release_artifacts", description="Clean local files from the bridge release tree.")
    parser.add_argument("--root", default=str(ROOT), help="Bridge release root to clean.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be removed without deleting files.")
    args = parser.parse_args(argv)

    artifacts = clean_artifacts(args.root, dry_run=args.dry_run)
    action = "Would remove" if args.dry_run else "Removed"
    for path in artifacts:
        print("%s %s" % (action, path))
    print("%s %s artifact(s)." % (action, len(artifacts)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
